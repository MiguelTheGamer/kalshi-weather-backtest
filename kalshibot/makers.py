"""What did liquidity providers (makers) actually earn on Kalshi?

Every trade records the taker's side; the maker held the other side at the
complementary price. So realized maker P&L needs no fill assumptions: it
already includes being picked off by better-informed takers. Maker fees
(0.0175 x C x P x (1 - P), rounded up per fill) are charged on every fill.

Caveat: this is what makers *as a class* earned. A slow newcomer gets filled
behind faster makers and is picked off more, so treat a GO as an upper bound.

Pre-committed rule (set before running): GO candidate only if a segment is
profitable after fees in BOTH time halves, its pooled ROI exceeds 2 standard
errors, and each half has >= 200 markets and >= 50 events.
"""
import json
import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass

from . import kalshi
from .config import DATA_DIR
from .http import get_json
from .longshot import BUCKETS, MAKER_FEE_RATE, _ts, bucket_of, markets_closing_in

MAX_TRADE_PAGES = 10          # skip (don't truncate) markets with more trades than this
PER_EVENT = 3
LATE_WINDOW_S = 2 * 3600      # trades within 2h of close count as "late"
MIN_MARKETS_PER_HALF = 200
MIN_EVENTS_PER_HALF = 50
CACHE = DATA_DIR / "cache"


def fill_fee(price: float, count: float) -> float:
    return math.ceil(round(MAKER_FEE_RATE * count * price * (1 - price) * 100, 6)) / 100


@dataclass(frozen=True)
class Fill:
    market: str
    event: str
    category: str
    close_ts: int
    late: bool
    price: float      # what the maker paid for the side they ended up holding
    count: float
    won: bool

    @property
    def cost(self) -> float:
        return self.count * self.price + fill_fee(self.price, self.count)

    @property
    def gross_pnl(self) -> float:
        return self.count * ((1.0 if self.won else 0.0) - self.price)

    @property
    def pnl(self) -> float:
        return self.gross_pnl - fill_fee(self.price, self.count)


def maker_fill(trade: dict, result: str, event: str, category: str, close_ts: int) -> Fill | None:
    taker = trade.get("taker_side")
    if taker not in ("yes", "no") or trade.get("is_block_trade"):
        return None
    maker_side = "no" if taker == "yes" else "yes"
    price = float(trade[f"{maker_side}_price_dollars"])
    count = float(trade["count_fp"])
    if not (0 < price < 1) or count <= 0:
        return None
    late = close_ts - _ts(trade["created_time"]) <= LATE_WINDOW_S
    return Fill(trade["ticker"], event, category, close_ts, late, price, count, result == maker_side)


def market_trades(ticker: str) -> list[dict] | None:
    """All trades for a settled market, or None if it has too many to fetch whole."""
    path = CACHE / f"trades_{ticker}.json"
    if path.exists():
        return json.loads(path.read_text())
    out, cursor = [], None
    for _ in range(MAX_TRADE_PAGES):
        q = {"ticker": ticker, "limit": 1000}
        if cursor:
            q["cursor"] = cursor
        page = get_json(f"{kalshi.MARKET_DATA_URL}/markets/trades", q)
        out.extend(page.get("trades", []))
        cursor = page.get("cursor")
        if not cursor:
            break
    else:
        return None
    keep = ("ticker", "taker_side", "yes_price_dollars", "no_price_dollars", "count_fp",
            "created_time", "is_block_trade")
    slim = [{k: t.get(k) for k in keep} for t in out]
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(slim))
    return slim


def series_categories() -> dict[str, str]:
    path = CACHE / "series_categories.json"
    if path.exists():
        return json.loads(path.read_text())
    s = get_json(f"{kalshi.MARKET_DATA_URL}/series", {}).get("series", [])
    cats = {x["ticker"]: x.get("category") or "Unknown" for x in s}
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cats))
    return cats


def sample_markets(n_markets: int, seed: int) -> list[dict]:
    """Settled, liquid, binary markets from the cached longshot windows, few per event."""
    rng = random.Random(seed)
    pool = []
    for f in sorted(CACHE.glob("settled_*.json")):
        pool.extend(json.loads(f.read_text()))
    by_event = defaultdict(list)
    for m in pool:
        if (m.get("market_type") == "binary" and m.get("result") in ("yes", "no")
                and float(m.get("volume_fp") or 0) >= 500):
            by_event[m["event_ticker"]].append(m)
    picked = []
    for ms in by_event.values():
        rng.shuffle(ms)
        picked.extend(ms[:PER_EVENT])
    rng.shuffle(picked)
    return picked[:n_markets]


def clustered_roi(fills: list[Fill], net: bool = True) -> tuple[float, float]:
    per_event = defaultdict(lambda: [0.0, 0.0])
    for f in fills:
        per_event[f.event][0] += f.cost
        per_event[f.event][1] += f.pnl if net else f.gross_pnl
    cost = sum(c for c, _ in per_event.values())
    if cost <= 0:
        return float("nan"), float("nan")
    roi = sum(p for _, p in per_event.values()) / cost
    resid = [p - roi * c for c, p in per_event.values()]
    k = len(resid)
    se = math.sqrt(k / max(k - 1, 1) * sum(r * r for r in resid)) / cost if k > 1 else float("nan")
    return roi, se


def segment_table(fills: list[Fill], key) -> dict:
    groups = defaultdict(list)
    for f in fills:
        groups[key(f)].append(f)
    table = {}
    for k, fs in sorted(groups.items()):
        roi, se = clustered_roi(fs)
        gross, _ = clustered_roi(fs, net=False)
        table[k] = {"markets": len({f.market for f in fs}), "events": len({f.event for f in fs}),
                    "fills": len(fs), "contracts": round(sum(f.count for f in fs)),
                    "win_rate": round(sum(f.count for f in fs if f.won) / sum(f.count for f in fs), 4),
                    "avg_price": round(sum(f.price * f.count for f in fs) / sum(f.count for f in fs), 4),
                    "gross_roi": round(gross, 4), "roi": round(roi, 4), "se": round(se, 4)}
    return table


SEGMENTS = {
    "overall": lambda f: "all",
    "by_price": lambda f: bucket_of(f.price),
    "by_timing_price": lambda f: f"{'late' if f.late else 'early'}|{bucket_of(f.price)}",
    "by_category": lambda f: f.category,
    "by_category_price": lambda f: f"{f.category}|{bucket_of(f.price)}",
}


def go_candidates(pooled: dict, first: dict, second: dict) -> list[str]:
    out = []
    for seg in SEGMENTS:
        for k, row in pooled[seg].items():
            a, b = first[seg].get(k), second[seg].get(k)
            if not a or not b:
                continue
            if min(a["markets"], b["markets"]) < MIN_MARKETS_PER_HALF:
                continue
            if min(a["events"], b["events"]) < MIN_EVENTS_PER_HALF:
                continue
            if a["roi"] > 0 and b["roi"] > 0 and row["roi"] > 2 * row["se"]:
                out.append(f"{seg}:{k} pooled {row['roi']:+.1%}±{row['se']:.1%}, "
                           f"halves {a['roi']:+.1%} / {b['roi']:+.1%}")
    return out


def run(n_markets: int = 1500, seed: int = 11) -> dict:
    cats = series_categories()
    fills: list[Fill] = []
    skipped = 0
    markets = sample_markets(n_markets, seed)
    for i, m in enumerate(markets, 1):
        trades = market_trades(m["ticker"])
        if trades is None:
            skipped += 1
            continue
        close = _ts(m["close_time"])
        cat = cats.get(m["event_ticker"].split("-")[0], "Unknown")
        for t in trades:
            f = maker_fill(t, m["result"], m["event_ticker"], cat, close)
            if f:
                fills.append(f)
        if i % 100 == 0:
            print(f"{i}/{len(markets)} markets, {len(fills)} fills, {skipped} skipped (too many trades)", flush=True)

    closes = sorted({f.close_ts for f in fills})
    mid = closes[len(closes) // 2]
    halves = ([f for f in fills if f.close_ts < mid], [f for f in fills if f.close_ts >= mid])
    result = {"meta": {"markets_sampled": len(markets), "skipped_too_many_trades": skipped,
                       "fills": len(fills), "split_ts": mid}}
    for name, fs in (("pooled", fills), ("first_half", halves[0]), ("second_half", halves[1])):
        result[name] = {seg: segment_table(fs, key) for seg, key in SEGMENTS.items()}
    result["go"] = go_candidates(result["pooled"], result["first_half"], result["second_half"])
    (DATA_DIR / "maker_results.json").write_text(json.dumps(result, indent=1))
    return result


def print_report() -> None:
    r = json.loads((DATA_DIR / "maker_results.json").read_text())
    print("meta:", r["meta"])
    for seg in ("overall", "by_category", "by_price", "by_timing_price"):
        print(f"\n== {seg} (pooled; halves) ==")
        print(f"{'segment':<26}{'mkts':>6}{'evts':>6}{'contracts':>11}{'price':>7}{'win%':>7}"
              f"{'gross':>8}{'net ROI':>16}{'half1':>8}{'half2':>8}")
        for k, row in r["pooled"][seg].items():
            a = r["first_half"][seg].get(k, {}).get("roi")
            b = r["second_half"][seg].get(k, {}).get("roi")
            fmt = lambda x: f"{x:+.1%}" if isinstance(x, float) else "-"
            print(f"{k:<26}{row['markets']:>6}{row['events']:>6}{row['contracts']:>11}{row['avg_price']:>7.3f}"
                  f"{row['win_rate']:>7.1%}{row['gross_roi']:>+8.1%}{row['roi']:>+9.1%}±{row['se']:<5.1%}"
                  f"{fmt(a):>8}{fmt(b):>8}")
    print("\nGO candidates:")
    for line in r["go"] or ["none -> NO-GO"]:
        print("  ", line)

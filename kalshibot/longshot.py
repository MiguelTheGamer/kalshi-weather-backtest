"""Favorite-longshot test: are cheap Kalshi contracts overpriced after fees?

Sample settled, non-parlay markets from random 1-hour close windows, read the
bid/ask from hourly candles at fixed leads before close, and score four ways
to trade each price bucket, fees included:
  taker_yes  buy YES at the ask          taker_no  buy NO at 1 - bid
  maker_yes  buy YES at the bid (fill    maker_no  buy NO at 1 - ask (fill
             assumed: an upper bound)              assumed: an upper bound)

Pre-committed rule (set before running): GO only if one strategy x bucket is
profitable after fees in BOTH time halves, with >= 200 markets and >= 50
distinct events in each half. Otherwise NO-GO.
"""
import calendar
import json
import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass

from . import kalshi
from .backtest import _price_at
from .brackets import taker_fee
from .config import DATA_DIR
from .http import get_json

MAKER_FEE_RATE = 0.0175  # Kalshi's maker fee where charged; assumed everywhere (conservative)
MIN_VOLUME = 500
MAX_SPREAD = 0.10
MAX_PER_EVENT = 10
LEADS_H = (24, 2)
BUCKETS = (0.0, 0.03, 0.06, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.94, 0.97, 1.0001)
STRATEGIES = ("taker_yes", "taker_no", "maker_yes", "maker_no")
MIN_MARKETS_PER_HALF = 200
MIN_EVENTS_PER_HALF = 50
CACHE = DATA_DIR / "cache"


def maker_fee(price: float) -> float:
    return math.ceil(round(MAKER_FEE_RATE * price * (1 - price) * 100, 6)) / 100


def _ts(iso: str) -> int:
    return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))


def markets_closing_in(start_ts: int, end_ts: int) -> list[dict]:
    path = CACHE / f"settled_{start_ts}_{end_ts}.json"
    if path.exists():
        return json.loads(path.read_text())
    out, cursor = [], None
    while True:
        q = {"status": "settled", "min_close_ts": start_ts, "max_close_ts": end_ts,
             "limit": 1000, "mve_filter": "exclude"}
        if cursor:
            q["cursor"] = cursor
        page = get_json(f"{kalshi.MARKET_DATA_URL}/markets", q)
        out.extend(page.get("markets", []))
        cursor = page.get("cursor")
        if not cursor:
            break
    keep = ("ticker", "event_ticker", "open_time", "close_time", "result", "volume_fp", "market_type")
    slim = [{k: m.get(k) for k in keep} for m in out]
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(slim))
    return slim


def eligible(markets: list[dict], rng: random.Random) -> list[dict]:
    by_event = defaultdict(list)
    for m in markets:
        if (m.get("market_type") == "binary" and m.get("result") in ("yes", "no")
                and float(m.get("volume_fp") or 0) >= MIN_VOLUME):
            by_event[m["event_ticker"]].append(m)
    out = []
    for ms in by_event.values():
        rng.shuffle(ms)
        out.extend(ms[:MAX_PER_EVENT])
    return out


@dataclass(frozen=True)
class Obs:
    lead: int
    close_ts: int
    event: str
    series: str
    bid: float
    ask: float
    yes_won: bool

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


def snapshots(markets: list[dict], window_start: int, window_end: int) -> list[Obs]:
    if not markets:
        return []
    tickers = [m["ticker"] for m in markets]
    candles = kalshi.batch_candles(tickers, window_start - max(LEADS_H) * 3600 - 4 * 3600,
                                   window_end, cache=True)
    out = []
    for m in markets:
        close, opened = _ts(m["close_time"]), _ts(m["open_time"])
        for lead in LEADS_H:
            ts = close - lead * 3600
            if ts <= opened:
                continue
            px = _price_at(candles.get(m["ticker"], []), ts)
            if px is None:
                continue
            bid, ask = px
            if not (0 < ask <= 1) or ask - bid > MAX_SPREAD or ask <= bid:
                continue
            out.append(Obs(lead, close, m["event_ticker"], m["event_ticker"].split("-")[0],
                           bid, ask, m["result"] == "yes"))
    return out


def trade(strategy: str, o: Obs) -> tuple[float, float] | None:
    """(cost, payout) per contract, or None if that order isn't possible."""
    if strategy == "taker_yes":
        p, fee, wins = o.ask, taker_fee(o.ask), o.yes_won
    elif strategy == "taker_no":
        p = round(1 - o.bid, 4)
        fee, wins = taker_fee(p), not o.yes_won
    elif strategy == "maker_yes":
        p, fee, wins = o.bid, maker_fee(o.bid), o.yes_won
    else:
        p = round(1 - o.ask, 4)
        fee, wins = maker_fee(p), not o.yes_won
    if not (0 < p < 1):
        return None
    return p + fee, 1.0 if wins else 0.0


def bucket_of(mid: float) -> str:
    for lo, hi in zip(BUCKETS, BUCKETS[1:]):
        if lo <= mid < hi:
            return f"{lo:.2f}-{min(hi, 1):.2f}"
    raise ValueError(mid)


def score(obs: list[Obs]) -> dict:
    """ROI per lead/bucket/strategy with event-clustered standard errors."""
    groups = defaultdict(list)
    for o in obs:
        groups[(o.lead, bucket_of(o.mid))].append(o)
    table = {}
    for (lead, b), os_ in sorted(groups.items()):
        row = {"n": len(os_), "events": len({o.event for o in os_}),
               "avg_mid": round(sum(o.mid for o in os_) / len(os_), 4),
               "yes_rate": round(sum(o.yes_won for o in os_) / len(os_), 4)}
        for s in STRATEGIES:
            per_event = defaultdict(lambda: [0.0, 0.0])
            for o in os_:
                t = trade(s, o)
                if t:
                    per_event[o.event][0] += t[0]
                    per_event[o.event][1] += t[1]
            cost = sum(c for c, _ in per_event.values())
            if cost <= 0:
                continue
            roi = sum(p - c for c, p in per_event.values()) / cost
            # cluster SE of a ratio estimator: residuals summed per event
            resid = [(p - c) - roi * c for c, p in per_event.values()]
            k = len(resid)
            se = math.sqrt(k / max(k - 1, 1) * sum(r * r for r in resid)) / cost if k > 1 else float("nan")
            row[s] = {"roi": round(roi, 4), "se": round(se, 4)}
        table[f"{lead}h|{b}"] = row
    return table


def go_no_go(first: dict, second: dict) -> list[str]:
    winners = []
    for key in first.keys() & second.keys():
        a, b = first[key], second[key]
        if min(a["n"], b["n"]) < MIN_MARKETS_PER_HALF or min(a["events"], b["events"]) < MIN_EVENTS_PER_HALF:
            continue
        for s in STRATEGIES:
            if s in a and s in b and a[s]["roi"] > 0 and b[s]["roi"] > 0:
                winners.append(f"{key} {s}: first {a[s]['roi']:+.1%}, second {b[s]['roi']:+.1%}")
    return sorted(winners)


def print_report() -> None:
    result = json.loads((DATA_DIR / "longshot_results.json").read_text())

    def cell(row: dict, s: str) -> str:
        return f"{row[s]['roi']:+6.1%}±{row[s]['se']:.1%}" if s in row else "      -      "

    for part in ("all", "first_half", "second_half"):
        print(f"\n== {part} ==   ROI after fees ± event-clustered SE")
        print(f"{'lead|bucket':<16}{'n':>6}{'evts':>6}{'mid':>7}{'yes%':>7}  "
              + "  ".join(f"{s:<13}" for s in STRATEGIES))
        for key, row in result[part].items():
            print(f"{key:<16}{row['n']:>6}{row['events']:>6}{row['avg_mid']:>7.3f}{row['yes_rate']:>7.1%}  "
                  + "  ".join(cell(row, s) for s in STRATEGIES))
    print("\nGO candidates (profitable in both halves, sample rule met):")
    for line in result["go"] or ["none -> NO-GO"]:
        print("  ", line)


def run(days: int = 120, windows: int = 48, seed: int = 7) -> dict:
    rng = random.Random(seed)
    now = int(time.time())
    end_all = now - 2 * 86400  # leave time for settlement
    span = days * 86400
    stride = span // windows
    obs: list[Obs] = []
    for i in range(windows):
        w_start = end_all - span + i * stride + rng.randrange(0, stride - 3600)
        w_start -= w_start % 3600
        ms = eligible(markets_closing_in(w_start, w_start + 3600), rng)
        obs.extend(snapshots(ms, w_start, w_start + 3600))
        print(f"window {i + 1}/{windows}: {len(ms)} markets, {len(obs)} snapshots total", flush=True)
    # Kalshi's public API only serves roughly the last two months of settled
    # markets, so split the data we actually got at its median close time.
    closes = sorted(o.close_ts for o in obs)
    midpoint = closes[len(closes) // 2] if closes else end_all
    first = [o for o in obs if o.close_ts < midpoint]
    second = [o for o in obs if o.close_ts >= midpoint]
    result = {"all": score(obs), "first_half": score(first), "second_half": score(second)}
    result["go"] = go_no_go(result["first_half"], result["second_half"])
    series_counts = defaultdict(int)
    for o in obs:
        series_counts[o.series] += 1
    result["top_series"] = sorted(series_counts.items(), key=lambda kv: -kv[1])[:25]
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "longshot_results.json").write_text(json.dumps(result, indent=1))
    return result

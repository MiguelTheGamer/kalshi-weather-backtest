"""Would a NEW maker order at 97-99c actually get filled? Live paper simulation.

When an open market's favorite side has a best bid of 97-99c, place a virtual
order at the back of that queue (queue_ahead = resting size at that price).
Then watch the real trade tape: long-shot takers trading at exactly our price
consume the queue in time priority, and a trade below our price means the level
was swept. Cancellations ahead of us are ignored, so this understates fills
(conservative). No real orders are ever placed.
"""
import json
import logging
import time
from dataclasses import asdict, dataclass
from statistics import median

from . import kalshi
from .config import DATA_DIR
from .http import get_json
from .longshot import _ts
from .makers import series_categories

log = logging.getLogger(__name__)

STATE = DATA_DIR / "queue_state.json"
MIN_PRICE, MAX_PRICE = 0.97, 0.99
ENTRY_WINDOW_S = 2 * 3600     # only join in the last 2h before close (where the edge was)
LATE_GRACE_S = 3600           # games often run past their expected end
DONE_AFTER_END_S = 3 * 3600   # stop watching an order this long after the expected end
MIN_VOLUME_24H = 500
MAX_ACTIVE = 60
ORDER_SIZE = 10.0
REFRESH_S = 600
POLL_S = 90


@dataclass
class VirtualOrder:
    ticker: str
    category: str
    fav_side: str           # side we buy: "yes" or "no"
    price: float            # our bid on fav_side
    queue_ahead: float      # contracts in front of us at t0
    t0: int
    close_ts: int
    consumed: float = 0.0   # long-shot taker volume at our price since t0
    filled_ts: int | None = None
    swept: bool = False
    done: bool = False
    last_trade_ts: int = 0

    @property
    def filled(self) -> bool:
        return self.filled_ts is not None


def best_fav_bid(book: dict, fav_side: str) -> tuple[float, float] | None:
    """(price, size) of the best bid on the favorite side."""
    levels = book.get("orderbook_fp", {}).get(f"{fav_side}_dollars") or []
    if not levels:
        return None
    price, size = max(((float(p), float(s)) for p, s in levels), key=lambda x: x[0])
    return price, size


def apply_trades(order: VirtualOrder, trades: list[dict]) -> None:
    """Advance the queue with long-shot takers' trades (oldest first)."""
    longshot = "no" if order.fav_side == "yes" else "yes"
    for t in sorted(trades, key=lambda t: t["created_time"]):
        ts = _ts(t["created_time"])
        if ts < order.t0 or order.filled:
            continue
        if t.get("taker_side") != longshot:
            continue
        fav_price = round(float(t[f"{order.fav_side}_price_dollars"]), 4)
        if fav_price < order.price - 1e-9:
            order.swept, order.filled_ts = True, ts
        elif abs(fav_price - order.price) < 1e-9:
            order.consumed += float(t["count_fp"])
            if order.consumed >= order.queue_ahead + ORDER_SIZE:
                order.filled_ts = ts


def expected_end(m: dict) -> int:
    """While open, sports close_time is the latest possible settlement (days out);
    expected_expiration_time is when the game should actually end."""
    return _ts(m.get("expected_expiration_time") or m["close_time"])


def find_candidates(cats: dict[str, str], now: int, tracked: set[str]) -> list[VirtualOrder]:
    out, cursor = [], None
    while True:
        q = {"status": "open", "limit": 1000, "mve_filter": "exclude"}
        if cursor:
            q["cursor"] = cursor
        page = get_json(f"{kalshi.MARKET_DATA_URL}/markets", q)
        for m in page.get("markets", []):
            if m["ticker"] in tracked or float(m.get("volume_24h_fp") or 0) < MIN_VOLUME_24H:
                continue
            # games often run past their expected end, so allow up to an hour after it
            if not (now - LATE_GRACE_S < expected_end(m) <= now + ENTRY_WINDOW_S):
                continue
            bid = float(m.get("yes_bid_dollars") or 0)
            ask = float(m.get("yes_ask_dollars") or 1)
            fav = "yes" if MIN_PRICE <= bid <= MAX_PRICE else "no" if MIN_PRICE <= 1 - ask <= MAX_PRICE else None
            if fav:
                cat = cats.get(m["event_ticker"].split("-")[0], "Unknown")
                out.append((cat != "Sports", -float(m.get("volume_24h_fp") or 0), m, fav, cat))
        cursor = page.get("cursor")
        if not cursor:
            break
    out.sort(key=lambda x: x[:2])  # sports first, then by volume
    orders = []
    for _, _, m, fav, cat in out:
        book = get_json(f"{kalshi.MARKET_DATA_URL}/markets/{m['ticker']}/orderbook", {})
        best = best_fav_bid(book, fav)
        if not best or not (MIN_PRICE <= best[0] <= MAX_PRICE):
            continue
        orders.append(VirtualOrder(m["ticker"], cat, fav, best[0], best[1], now, expected_end(m)))
    return orders


def _save(orders: list[VirtualOrder]) -> None:
    STATE.write_text(json.dumps([asdict(o) for o in orders], indent=1))


def _load() -> list[VirtualOrder]:
    return [VirtualOrder(**d) for d in json.loads(STATE.read_text())] if STATE.exists() else []


def watch(duration_s: int) -> None:
    cats = series_categories()
    orders = _load()
    end = time.time() + duration_s
    last_refresh = 0.0
    while time.time() < end:
        now = int(time.time())
        active = [o for o in orders if not o.done]
        if now - last_refresh >= REFRESH_S and len(active) < MAX_ACTIVE:
            new = find_candidates(cats, now, {o.ticker for o in orders})[: MAX_ACTIVE - len(active)]
            orders.extend(new)
            last_refresh = now
            print(f"{time.strftime('%H:%M:%S')} +{len(new)} orders ({len(orders)} total)", flush=True)
        for o in [o for o in orders if not o.done]:
            since = max(o.t0, o.last_trade_ts) - 5
            try:
                trades = get_json(f"{kalshi.MARKET_DATA_URL}/markets/trades",
                                  {"ticker": o.ticker, "min_ts": since, "limit": 1000}).get("trades", [])
            except Exception as e:  # keep watching other markets; retry this one next cycle
                log.warning("trades fetch failed for %s: %s", o.ticker, e)
                continue
            apply_trades(o, trades)
            if trades:
                o.last_trade_ts = max(_ts(t["created_time"]) for t in trades)
            if o.filled or now > o.close_ts + DONE_AFTER_END_S:
                o.done = True
        _save(orders)
        time.sleep(POLL_S)
    for o in orders:  # anything still open counts as unfilled for this session
        o.done = True
    _save(orders)


def report() -> dict:
    """Fill stats plus P&L for filled orders that have settled."""
    orders = _load()
    results = {}
    for o in orders:
        if o.filled:
            m = get_json(f"{kalshi.MARKET_DATA_URL}/markets/{o.ticker}", {}).get("market", {})
            results[o.ticker] = m.get("result")
    groups: dict[str, list[VirtualOrder]] = {}
    for o in orders:
        groups.setdefault("Sports" if o.category == "Sports" else "Other", []).append(o)
        groups.setdefault("All", []).append(o)
    summary = {}
    for g, os_ in groups.items():
        filled = [o for o in os_ if o.filled]
        settled = [o for o in filled if results.get(o.ticker) in ("yes", "no")]
        pnl = sum(ORDER_SIZE * ((1.0 if results[o.ticker] == o.fav_side else 0.0) - o.price) for o in settled)
        summary[g] = {
            "orders": len(os_), "filled": len(filled),
            "fill_rate": round(len(filled) / len(os_), 3) if os_ else None,
            "median_queue_ahead": median(o.queue_ahead for o in os_) if os_ else None,
            "median_minutes_to_fill": round(median((o.filled_ts - o.t0) / 60 for o in filled), 1) if filled else None,
            "median_pct_of_queue_cleared_if_unfilled": round(median(
                min(1.0, o.consumed / (o.queue_ahead + ORDER_SIZE)) for o in os_ if not o.filled), 3)
            if any(not o.filled for o in os_) else None,
            "settled_fills": len(settled), "wins": sum(results[o.ticker] == o.fav_side for o in settled),
            "paper_pnl_dollars": round(pnl, 2),
        }
    (DATA_DIR / "queue_report.json").write_text(json.dumps(summary, indent=1))
    return summary

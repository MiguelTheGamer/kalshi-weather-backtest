"""Walk-forward backtest on settled Kalshi events.

For each past city-day we only use information available at decision time:
  * lead=2: decide at 15:00 local standard time the day before, using model
    runs issued >= 48h ahead of each hour (Open-Meteo previous_day2).
  * lead=1: decide at 08:00 LST on the day, using runs issued 24h ahead.
Bias/sigma are fit on earlier days only (rolling window), prices come from
Kalshi's hourly candles at that moment, and fills are assumed at the ask.
"""
import json
import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import kalshi
from .brackets import Bracket, parse_bracket
from .config import DATA_DIR, RiskConfig
from .model import Calibration, event_probs
from .stations import STATIONS, Station
from .strategy import find_trades, settle_pnl
from .weather import climate_day_window, historical_forecasts

log = logging.getLogger(__name__)

CAL_WINDOW = 60
CAL_MIN = 20
DECISION_OFFSET_H = {2: -9, 1: 8}  # hours from climate-day start (LST midnight)
MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def event_date(event_ticker: str) -> date:
    """KXHIGHNY-26OCT10 -> 2026-10-10."""
    code = event_ticker.rsplit("-", 1)[1]
    return date(2000 + int(code[:2]), MONTHS[code[2:5]], int(code[5:]))


@dataclass(frozen=True)
class PastEvent:
    day: date
    markets: tuple[dict, ...]
    actual: int


def load_settled(series: str, since: date) -> list[PastEvent]:
    out = []
    for ev in kalshi.list_events(series, "settled", max_events=600):
        try:
            d = event_date(ev["event_ticker"])
        except (ValueError, KeyError, IndexError):
            continue
        ms = ev.get("markets") or []
        vals = {m.get("expiration_value") for m in ms if m.get("expiration_value")}
        if d < since or len(vals) != 1:
            continue
        out.append(PastEvent(d, tuple(ms), round(float(vals.pop()))))
    return sorted(out, key=lambda e: e.day)


def rolling_calibration(residuals: list[float]) -> Calibration | None:
    recent = residuals[-CAL_WINDOW:]
    if len(recent) < CAL_MIN:
        return None
    return Calibration(statistics.fmean(recent), statistics.stdev(recent), len(recent))


def _price_at(candles: list[dict], ts: int) -> tuple[float, float] | None:
    best = None
    for c in candles:
        if c["end_period_ts"] <= ts and (best is None or c["end_period_ts"] > best["end_period_ts"]):
            best = c
    if best is None or ts - best["end_period_ts"] > 3 * 3600:
        return None
    bid = best.get("yes_bid", {}).get("close_dollars")
    ask = best.get("yes_ask", {}).get("close_dollars")
    if bid is None or ask is None:
        return None
    return float(bid), float(ask)


def _brackets_at(ev: PastEvent, candles: dict[str, list[dict]], ts: int) -> list[Bracket] | None:
    out = []
    for m in ev.markets:
        px = _price_at(candles.get(m["ticker"], []), ts)
        if px is None:
            return None
        b = parse_bracket(m)
        out.append(Bracket(b.ticker, b.lo, b.hi, px[0], px[1], b.label))
    return out


@dataclass
class Result:
    series: str
    lead: int
    days: int = 0
    trades: int = 0
    wins: int = 0
    staked: float = 0.0
    pnl: float = 0.0
    model_logloss: list[float] = field(default_factory=list)
    market_logloss: list[float] = field(default_factory=list)
    calibration: Calibration | None = None
    log_rows: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        mean = lambda xs: round(statistics.fmean(xs), 4) if xs else None
        return {
            "series": self.series, "city": STATIONS[self.series].city, "lead": self.lead,
            "days": self.days, "trades": self.trades,
            "win_rate": round(self.wins / self.trades, 3) if self.trades else None,
            "staked": round(self.staked, 2), "pnl": round(self.pnl, 2),
            "roi": round(self.pnl / self.staked, 3) if self.staked else None,
            "model_logloss": mean(self.model_logloss), "market_logloss": mean(self.market_logloss),
        }


def _market_probs(brackets: list[Bracket]) -> dict[str, float]:
    mids = {b.ticker: max((b.yes_bid + b.yes_ask) / 2, 0.005) for b in brackets}
    total = sum(mids.values())
    return {t: v / total for t, v in mids.items()}


def run_series(series: str, lead: int, since: date, cfg: RiskConfig) -> Result:
    st: Station = STATIONS[series]
    res = Result(series, lead)
    events = load_settled(series, since - timedelta(days=CAL_WINDOW + 10))
    if not events:
        return res
    fc = historical_forecasts(st, events[0].day, events[-1].day, lead)
    residuals: list[float] = []

    for ev in events:
        f = fc.get(ev.day)
        if f is None:
            continue
        cal = rolling_calibration(residuals)
        residuals.append(ev.actual - f)  # appended after use: no peeking
        if cal is None or ev.day < since:
            continue
        start, _ = climate_day_window(st, ev.day)
        ts = int((start + timedelta(hours=DECISION_OFFSET_H[lead])).timestamp())
        candles = kalshi.batch_candles([m["ticker"] for m in ev.markets], ts - 4 * 3600, ts + 3600)
        brackets = _brackets_at(ev, candles, ts)
        if not brackets:
            continue
        probs = event_probs(brackets, f, cal)
        mkt = _market_probs(brackets)
        winner = next((b.ticker for b in brackets if b.contains(ev.actual)), None)
        if winner is None:
            continue
        res.days += 1
        res.model_logloss.append(-math.log(max(probs[winner], 1e-4)))
        res.market_logloss.append(-math.log(max(mkt[winner], 1e-4)))

        for t in find_trades(brackets, probs, cfg):
            pnl = settle_pnl(t.side, t.price, t.contracts, t.ticker == winner)
            res.trades += 1
            res.wins += pnl > 0
            res.staked += t.cost
            res.pnl += pnl
            res.log_rows.append({"day": ev.day.isoformat(), "ticker": t.ticker, "side": t.side,
                                 "price": t.price, "prob": round(t.prob, 3), "contracts": t.contracts,
                                 "forecast": round(f, 1), "actual": ev.actual, "pnl": round(pnl, 2)})
    res.calibration = rolling_calibration(residuals)
    return res


def save_calibration(results: list[Result]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / "calibration.json"
    existing = json.loads(path.read_text()) if path.exists() else {}
    for r in results:
        if r.calibration:
            existing.setdefault(r.series, {})[str(r.lead)] = {
                "bias": round(r.calibration.bias, 3), "sigma": round(r.calibration.sigma, 3),
                "n": r.calibration.n, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
    path.write_text(json.dumps(existing, indent=2))


def save_summary(results: list[Result]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / "backtest_summary.json"
    existing = json.loads(path.read_text()) if path.exists() else {}
    for r in results:
        existing.setdefault(r.series, {})[str(r.lead)] = r.summary()
    path.write_text(json.dumps(existing, indent=2))
    trades = DATA_DIR / "backtest_trades.jsonl"
    with trades.open("w") as fh:
        for r in results:
            for row in r.log_rows:
                fh.write(json.dumps({"series": r.series, "lead": r.lead, **row}) + "\n")


def load_calibration(series: str, lead: int) -> Calibration | None:
    path = DATA_DIR / "calibration.json"
    if not path.exists():
        return None
    entry = json.loads(path.read_text()).get(series, {}).get(str(lead))
    return Calibration(entry["bias"], entry["sigma"], entry["n"]) if entry else None

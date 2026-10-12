"""Which signals actually beat the market? Scored by log loss on the winning bracket.

Strategies compared at several decision times:
  * market       - Kalshi mid prices (the bar to beat)
  * model        - forecast + calibration only
  * blend(w)     - geometric pool: market^(1-w) * model^w
  * obs (intraday) - model conditioned on the max already observed at the station
"""
import csv
import io
import json
import math
import statistics
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from . import kalshi
from .backtest import (CAL_WINDOW, _brackets_at, _market_probs, load_settled,
                       rolling_calibration)
from .brackets import Bracket
from .config import DATA_DIR, RiskConfig
from .http import get_json
from .model import Calibration, event_probs
from .stations import STATIONS, Station
from .strategy import find_trades, settle_pnl
from .weather import MODELS, PREVIOUS_RUNS_URL, c_to_f, climate_day_window, consensus_from_hourly

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
BLEND_WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)


def blend(market: dict[str, float], model: dict[str, float], w: float) -> dict[str, float]:
    raw = {t: math.exp((1 - w) * math.log(max(market[t], 1e-4)) + w * math.log(max(model[t], 1e-4)))
           for t in market}
    total = sum(raw.values())
    return {t: v / total for t, v in raw.items()}


def iem_obs(st: Station, first: date, last: date) -> list[tuple[datetime, float]]:
    """(UTC time, best °F) for every archived METAR, using the 0.1°C T-group when present."""
    cache = DATA_DIR / "cache" / f"iem_{st.icao}_{first}_{last}.json"
    if cache.exists():
        return [(datetime.fromisoformat(t), v) for t, v in json.loads(cache.read_text())]
    q = (f"{IEM_URL}?station={st.icao[1:]}&data=tmpf&data=metar&tz=Etc/UTC&format=onlycomma"
         f"&latlon=no&missing=M&report_type=3&report_type=4"
         f"&year1={first.year}&month1={first.month}&day1={first.day}"
         f"&year2={last.year}&month2={last.month}&day2={last.day}")
    with urllib.request.urlopen(urllib.request.Request(q, headers={"User-Agent": "kalshibot"}), timeout=120) as r:
        text = r.read().decode()
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        if row["tmpf"] in ("M", ""):
            continue
        ts = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        temp = float(row["tmpf"])
        for tok in row["metar"].split():
            if len(tok) == 9 and tok[0] == "T" and tok[1:].isdigit():
                c = int(tok[2:5]) / 10 * (-1 if tok[1] == "1" else 1)
                temp = c_to_f(c)
        out.append((ts, temp))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([(t.isoformat(), v) for t, v in out]))
    return out


def observed_max(obs: list[tuple[datetime, float]], start: datetime, until: datetime) -> int | None:
    vals = [v for t, v in obs if start <= t < until]
    return round(max(vals)) if vals else None


def hourly_runs(st: Station, first: date, last: date) -> dict:
    var = "temperature_2m_previous_day1"
    data = get_json(PREVIOUS_RUNS_URL, {
        "latitude": st.lat, "longitude": st.lon, "hourly": var, "models": ",".join(MODELS),
        "temperature_unit": "fahrenheit", "timezone": "GMT",
        "start_date": first.isoformat(), "end_date": (last + timedelta(days=1)).isoformat(),
    })
    return {"hourly": data["hourly"], "keys": [f"{var}_{m}" for m in MODELS]}


@dataclass
class Score:
    n: int = 0
    loss: dict = None
    trades: int = 0
    staked: float = 0.0
    pnl: float = 0.0

    def __post_init__(self):
        self.loss = self.loss or {}

    def add(self, name: str, probs: dict[str, float], winner: str) -> None:
        self.loss.setdefault(name, []).append(-math.log(max(probs[winner], 1e-4)))

    def summary(self) -> dict:
        return {"days": self.n, "trades": self.trades, "staked": round(self.staked, 2),
                "pnl": round(self.pnl, 2), "roi": round(self.pnl / self.staked, 3) if self.staked else None,
                **{k: round(statistics.fmean(v), 4) for k, v in self.loss.items()}}


def intraday_study(series: str, since: date, hours_lst: tuple[int, ...], cfg: RiskConfig,
                   trade_with: str = "obs") -> dict[int, Score]:
    st = STATIONS[series]
    events = load_settled(series, since - timedelta(days=CAL_WINDOW + 10))
    if not events:
        return {}
    runs = hourly_runs(st, events[0].day, events[-1].day)
    obs = iem_obs(st, since - timedelta(days=1), events[-1].day + timedelta(days=1))
    scores = {h: Score() for h in hours_lst}
    residuals: list[float] = []

    for ev in events:
        start, end = climate_day_window(st, ev.day)
        full = consensus_from_hourly(runs["hourly"], runs["keys"], start, end)
        if full is None:
            continue
        cal = rolling_calibration(residuals)
        residuals.append(ev.actual - full)
        if cal is None or ev.day < since:
            continue
        candles = kalshi.batch_candles([m["ticker"] for m in ev.markets],
                                       int(start.timestamp()), int(end.timestamp()), cache=True)
        for h in hours_lst:
            decide = start + timedelta(hours=h)
            brackets = _brackets_at(ev, candles, int(decide.timestamp()))
            if not brackets:
                continue
            winner = next((b.ticker for b in brackets if b.contains(ev.actual)), None)
            if winner is None:
                continue
            m_obs = observed_max(obs, start, decide)
            rem = consensus_from_hourly(runs["hourly"], runs["keys"], start, end, after=decide)
            mkt = _market_probs(brackets)
            model = event_probs(brackets, full, cal)
            with_obs = event_probs(brackets, rem if rem is not None else -999, cal, m_obs)
            sc = scores[h]
            sc.n += 1
            sc.add("market", mkt, winner)
            sc.add("model", model, winner)
            sc.add("obs", with_obs, winner)
            for w in BLEND_WEIGHTS[1:-1]:
                sc.add(f"blend{w}", blend(mkt, with_obs, w), winner)
            chosen = {"obs": with_obs, "blend": blend(mkt, with_obs, 0.5)}[trade_with]
            for t in find_trades(brackets, chosen, cfg):
                sc.trades += 1
                sc.staked += t.cost
                sc.pnl += settle_pnl(t.side, t.price, t.contracts, t.ticker == winner)
    return scores

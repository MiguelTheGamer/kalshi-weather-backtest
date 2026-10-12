"""Forecasts (Open-Meteo, multi-model) and live observations (METAR)."""
import re
from datetime import date, datetime, timedelta, timezone

from .http import get_json
from .stations import Station

MODELS = ("gfs_seamless", "ecmwf_ifs025", "icon_seamless")
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
METAR_URL = "https://aviationweather.gov/api/data/metar"


def climate_day_window(st: Station, day: date) -> tuple[datetime, datetime]:
    """UTC [start, end) of the local-standard-time climate day."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=st.std_offset)
    return start, start + timedelta(hours=24)


def _window_max(times: list[str], temps: list, start: datetime, end: datetime,
                after: datetime | None = None) -> float | None:
    vals = []
    for t, v in zip(times, temps):
        ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        if v is None or not (start <= ts < end) or (after and ts < after):
            continue
        vals.append(v)
    return max(vals) if vals else None


def consensus_from_hourly(hourly: dict, keys: list[str], start: datetime, end: datetime,
                          after: datetime | None = None) -> float | None:
    """Mean across models of each model's max hourly temp in the window."""
    maxes = [m for k in keys if k in hourly
             if (m := _window_max(hourly["time"], hourly[k], start, end, after)) is not None]
    return sum(maxes) / len(maxes) if maxes else None


def live_forecast(st: Station, day: date, after: datetime | None = None) -> float | None:
    """Latest consensus forecast of the day's high (optionally only hours after `after`)."""
    data = get_json(FORECAST_URL, {
        "latitude": st.lat, "longitude": st.lon, "hourly": "temperature_2m",
        "models": ",".join(MODELS), "temperature_unit": "fahrenheit",
        "timezone": "GMT", "forecast_days": 4,
    })
    start, end = climate_day_window(st, day)
    keys = [f"temperature_2m_{m}" for m in MODELS]
    return consensus_from_hourly(data["hourly"], keys, start, end, after)


def historical_forecasts(st: Station, first: date, last: date, lead_days: int) -> dict[date, float]:
    """What the models predicted `lead_days` before each hour, for every day in range."""
    var = f"temperature_2m_previous_day{lead_days}"
    data = get_json(PREVIOUS_RUNS_URL, {
        "latitude": st.lat, "longitude": st.lon, "hourly": var,
        "models": ",".join(MODELS), "temperature_unit": "fahrenheit", "timezone": "GMT",
        "start_date": first.isoformat(), "end_date": (last + timedelta(days=1)).isoformat(),
    })
    keys = [f"{var}_{m}" for m in MODELS]
    out = {}
    d = first
    while d <= last:
        start, end = climate_day_window(st, d)
        f = consensus_from_hourly(data["hourly"], keys, start, end)
        if f is not None:
            out[d] = f
        d += timedelta(days=1)
    return out


_SIX_HR_MAX = re.compile(r"\s1([01])(\d{3})\s")


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def metar_max_f(ob: dict) -> float | None:
    """Highest temperature evidenced by one METAR: the reading, or its 6-hour max group."""
    vals = []
    if ob.get("temp") is not None:
        vals.append(c_to_f(ob["temp"]))
    m = _SIX_HR_MAX.search(f" {ob.get('rawOb', '')} ")
    if m:
        sign = -1 if m.group(1) == "1" else 1
        vals.append(c_to_f(sign * int(m.group(2)) / 10))
    return max(vals) if vals else None


def observed_max_so_far(st: Station, day: date) -> int | None:
    """Rounded max °F seen at the station so far in the climate day, or None."""
    start, end = climate_day_window(st, day)
    now = datetime.now(timezone.utc)
    if now <= start:
        return None
    hours = min(30, int((now - start).total_seconds() // 3600) + 1)
    obs = get_json(METAR_URL, {"ids": st.icao, "format": "json", "hours": hours})
    vals = []
    for ob in obs if isinstance(obs, list) else []:
        ts = datetime.fromtimestamp(ob["obsTime"], tz=timezone.utc)
        # A 6-hr max group covers the 6 hours before the report; only use it
        # when that whole span sits inside today's window.
        in_window = start <= ts < end
        if not in_window:
            continue
        if ts - timedelta(hours=6) < start:
            v = c_to_f(ob["temp"]) if ob.get("temp") is not None else None
        else:
            v = metar_max_f(ob)
        if v is not None:
            vals.append(v)
    return round(max(vals)) if vals else None

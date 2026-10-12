"""Probability that the official daily high lands in each bracket.

Final high = max(observed max so far, max of the remaining hours). The
remaining-hours max is modelled as Normal(forecast + bias, sigma), where bias
and sigma come from calibration against past Kalshi settlements. Before the
day starts there is no observation and this reduces to the plain forecast.
"""
import math
from dataclasses import dataclass

from .brackets import Bracket

MIN_SIGMA = 1.2
# The official high is usually a bit above the max of the hourly METARs (the
# true peak falls between readings). Measured May-Oct 2026 across NYC, Chicago
# and Miami (204 days): official - METAR max was 0 39% of the time, +1 52%,
# +2 7%. These defaults round that, leaning slightly toward larger offsets.
DEFAULT_OBS_OFFSETS: dict[int, float] = {0: 0.35, 1: 0.55, 2: 0.10}


@dataclass(frozen=True)
class Calibration:
    bias: float  # mean(actual - forecast)
    sigma: float  # std of the residual
    n: int = 0
    obs_offsets: tuple[tuple[int, float], ...] = tuple(DEFAULT_OBS_OFFSETS.items())


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_at_most(k: float, forecast: float, cal: Calibration, observed_max: int | None = None) -> float:
    """P(final whole-degree high <= k).

    final = max(official reading so far, max of remaining hours), where the
    official reading so far = observed METAR max + an offset drawn from
    cal.obs_offsets.
    """
    sigma = max(cal.sigma, MIN_SIGMA)
    p_rest = _norm_cdf((k + 0.5 - (forecast + cal.bias)) / sigma)
    if observed_max is None:
        return p_rest
    p_obs = sum(w for off, w in cal.obs_offsets if observed_max + off <= k)
    return p_obs * p_rest


def bracket_prob(b: Bracket, forecast: float, cal: Calibration, observed_max: int | None = None) -> float:
    upper = prob_at_most(b.hi, forecast, cal, observed_max)
    lower = prob_at_most(b.lo - 1, forecast, cal, observed_max)
    return min(1.0, max(0.0, upper - lower))


def event_probs(brackets: list[Bracket], forecast: float, cal: Calibration,
                observed_max: int | None = None) -> dict[str, float]:
    """Bracket probabilities, renormalised so the event sums to 1."""
    raw = {b.ticker: bracket_prob(b, forecast, cal, observed_max) for b in brackets}
    total = sum(raw.values())
    if total <= 0:
        return {t: 1.0 / len(raw) for t in raw}
    return {t: p / total for t, p in raw.items()}

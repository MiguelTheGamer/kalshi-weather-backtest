from datetime import date, datetime, timezone

import pytest

from kalshibot.backtest import _price_at, event_date, rolling_calibration
from kalshibot.brackets import NEG_INF, POS_INF, Bracket, parse_bracket, taker_fee
from kalshibot.config import RiskConfig
from kalshibot.model import Calibration, bracket_prob, event_probs, prob_at_most
from kalshibot.research import blend
from kalshibot.stations import STATIONS
from kalshibot.strategy import cap_daily, find_trades, settle_pnl
from kalshibot.weather import climate_day_window, consensus_from_hourly, metar_max_f

CAL = Calibration(bias=0.0, sigma=2.0, n=60)


def _market(kind, floor=None, cap=None, bid="0.40", ask="0.42", ticker="T"):
    return {"ticker": ticker, "strike_type": kind, "floor_strike": floor, "cap_strike": cap,
            "yes_bid_dollars": bid, "yes_ask_dollars": ask, "yes_sub_title": ""}


# ---------- brackets ----------

def test_parse_between_bracket_is_inclusive_both_ends():
    b = parse_bracket(_market("between", 69, 70))
    assert (b.lo, b.hi) == (69, 70)
    assert b.contains(69) and b.contains(70) and not b.contains(71)


def test_parse_less_bracket_excludes_cap():
    b = parse_bracket(_market("less", cap=69))
    assert (b.lo, b.hi) == (NEG_INF, 68)


def test_parse_greater_bracket_excludes_floor():
    b = parse_bracket(_market("greater", floor=76))
    assert (b.lo, b.hi) == (77, POS_INF)


def test_parse_unknown_strike_type_raises():
    with pytest.raises(ValueError):
        parse_bracket(_market("weird"))


def test_taker_fee_rounds_up_to_cent():
    assert taker_fee(0.50, 1) == 0.02          # 0.0175 -> 0.02
    assert taker_fee(0.50, 100) == 1.75
    assert taker_fee(0.99, 1) == 0.01


# ---------- model ----------

def test_event_probs_sum_to_one():
    brackets = [Bracket("a", NEG_INF, 68, 0, 0), Bracket("b", 69, 70, 0, 0),
                Bracket("c", 71, 72, 0, 0), Bracket("d", 73, POS_INF, 0, 0)]
    probs = event_probs(brackets, forecast=70.0, cal=CAL)
    assert sum(probs.values()) == pytest.approx(1.0)
    assert max(probs, key=probs.get) == "b"


def test_bias_shifts_distribution():
    b = Bracket("hot", 75, POS_INF, 0, 0)
    assert bracket_prob(b, 70, Calibration(3.0, 2.0)) > bracket_prob(b, 70, CAL)


def test_observation_rules_out_lower_brackets():
    # Already saw 75°F; remaining hours forecast to be cooler.
    assert prob_at_most(74, forecast=60, cal=CAL, observed_max=75) == 0.0
    # Official is usually 0-2°F above the METAR max, so 75 and 76 stay live.
    assert prob_at_most(75, forecast=60, cal=CAL, observed_max=75) == pytest.approx(0.35, abs=0.01)
    assert prob_at_most(77, forecast=60, cal=CAL, observed_max=75) == pytest.approx(1.0, abs=0.01)


# ---------- strategy ----------

def test_find_trades_buys_underpriced_yes():
    b = Bracket("x", 69, 70, yes_bid=0.18, yes_ask=0.20)
    trades = find_trades([b], {"x": 0.45}, RiskConfig())
    assert trades and trades[0].side == "yes" and trades[0].price == 0.20
    assert trades[0].edge == pytest.approx(0.45 - 0.20 - taker_fee(0.20))


def test_find_trades_buys_no_when_yes_overpriced():
    b = Bracket("x", 69, 70, yes_bid=0.60, yes_ask=0.62)
    trades = find_trades([b], {"x": 0.30}, RiskConfig())
    assert trades and trades[0].side == "no" and trades[0].price == pytest.approx(0.40)


def test_find_trades_skips_fair_prices_and_absurd_edges():
    cfg = RiskConfig()
    fair = Bracket("f", 69, 70, yes_bid=0.44, yes_ask=0.46)
    assert find_trades([fair], {"f": 0.47}, cfg) == []
    absurd = Bracket("a", 69, 70, yes_bid=0.05, yes_ask=0.06)
    assert find_trades([absurd], {"a": 0.95}, cfg) == []  # edge > max_edge


def test_stake_respects_per_market_cap():
    cfg = RiskConfig(bankroll=10_000)
    t = find_trades([Bracket("x", 69, 70, 0.18, 0.20)], {"x": 0.45}, cfg)[0]
    assert t.cost <= cfg.max_stake_per_market + 0.01


def test_cap_daily_keeps_best_edges_within_budget():
    cfg = RiskConfig(bankroll=1000, max_daily_stake=6.0)
    trades = find_trades([Bracket("a", 1, 2, 0.1, 0.20), Bracket("b", 3, 4, 0.1, 0.20)],
                         {"a": 0.50, "b": 0.35}, RiskConfig(bankroll=1000, max_trades_per_event=2))
    kept = cap_daily(trades, already_staked=0.0, cfg=cfg)
    assert [t.ticker for t in kept] == ["a"]


def test_settle_pnl_includes_fees():
    assert settle_pnl("yes", 0.40, 10, yes_won=True) == pytest.approx(10 - 4.0 - taker_fee(0.40, 10))
    assert settle_pnl("no", 0.40, 10, yes_won=True) == pytest.approx(-4.0 - taker_fee(0.40, 10))


# ---------- weather / backtest helpers ----------

def test_climate_day_uses_local_standard_time():
    start, end = climate_day_window(STATIONS["KXHIGHNY"], date(2026, 7, 4))
    assert start == datetime(2026, 7, 4, 5, tzinfo=timezone.utc)  # midnight EST, even in July
    assert (end - start).total_seconds() == 86400


def test_consensus_averages_model_window_maxes():
    hourly = {"time": ["2026-07-04T04:00", "2026-07-04T18:00", "2026-07-05T06:00"],
              "a": [99, 80, 99], "b": [99, 84, 99]}
    start, end = climate_day_window(STATIONS["KXHIGHNY"], date(2026, 7, 4))
    assert consensus_from_hourly(hourly, ["a", "b"], start, end) == 82


def test_metar_max_uses_six_hour_max_group():
    ob = {"temp": 20.0, "rawOb": "KNYC 041751Z 10SM CLR 20/10 A3000 RMK AO2 10256 20194"}
    assert metar_max_f(ob) == pytest.approx(25.6 * 9 / 5 + 32)


def test_event_date_parses_ticker():
    assert event_date("KXHIGHNY-26OCT10") == date(2026, 10, 10)


def test_rolling_calibration_needs_minimum_history():
    assert rolling_calibration([1.0] * 5) is None
    cal = rolling_calibration([1.0, -1.0] * 15)
    assert cal.bias == pytest.approx(0.0) and cal.sigma > 0


def test_price_at_picks_latest_candle_before_decision():
    candles = [{"end_period_ts": 100, "yes_bid": {"close_dollars": "0.1"}, "yes_ask": {"close_dollars": "0.2"}},
               {"end_period_ts": 200, "yes_bid": {"close_dollars": "0.3"}, "yes_ask": {"close_dollars": "0.4"}},
               {"end_period_ts": 300, "yes_bid": {"close_dollars": "0.5"}, "yes_ask": {"close_dollars": "0.6"}}]
    assert _price_at(candles, 250) == (0.3, 0.4)
    assert _price_at(candles, 50) is None


def test_blend_endpoints_match_inputs():
    mkt, mdl = {"a": 0.7, "b": 0.3}, {"a": 0.2, "b": 0.8}
    assert blend(mkt, mdl, 0.0)["a"] == pytest.approx(0.7)
    assert blend(mkt, mdl, 1.0)["a"] == pytest.approx(0.2)

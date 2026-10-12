import pytest

from kalshibot.brackets import taker_fee
from kalshibot.longshot import Obs, _ts, bucket_of, go_no_go, maker_fee, score, trade


def _obs(bid, ask, yes_won, event="E1", lead=24):
    return Obs(lead, 0, event, "S", bid, ask, yes_won)


def test_ts_parses_utc_regardless_of_local_dst():
    assert _ts("2026-07-04T00:00:00Z") == 1783123200


def test_trade_costs_include_fees():
    o = _obs(0.04, 0.06, yes_won=False)
    assert trade("taker_yes", o) == (pytest.approx(0.06 + taker_fee(0.06)), 0.0)
    assert trade("taker_no", o) == (pytest.approx(0.96 + taker_fee(0.96)), 1.0)
    assert trade("maker_no", o) == (pytest.approx(0.94 + maker_fee(0.94)), 1.0)


def test_trade_rejects_impossible_prices():
    assert trade("maker_yes", _obs(0.0, 0.02, False)) is None


def test_bucket_edges():
    assert bucket_of(0.0) == "0.00-0.03"
    assert bucket_of(0.05) == "0.03-0.06"
    assert bucket_of(1.0) == "0.97-1.00"


def test_score_reports_negative_roi_for_overpriced_longshots():
    obs = [_obs(0.09, 0.11, yes_won=False, event=f"E{i}") for i in range(20)]
    row = score(obs)["24h|0.10-0.20"]
    assert row["n"] == 20 and row["yes_rate"] == 0
    assert row["taker_yes"]["roi"] == -1.0
    assert row["taker_no"]["roi"] > 0


def test_go_requires_both_halves_and_sample_size():
    good = {"k": {"n": 300, "events": 80, "taker_no": {"roi": 0.02, "se": 0.01}}}
    bad = {"k": {"n": 300, "events": 80, "taker_no": {"roi": -0.01, "se": 0.01}}}
    small = {"k": {"n": 50, "events": 80, "taker_no": {"roi": 0.05, "se": 0.01}}}
    assert go_no_go(good, good) == ["k taker_no: first +2.0%, second +2.0%"]
    assert go_no_go(good, bad) == []
    assert go_no_go(good, small) == []

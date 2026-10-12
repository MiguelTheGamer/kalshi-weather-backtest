import pytest

from kalshibot.makers import Fill, clustered_roi, fill_fee, go_candidates, maker_fill

CLOSE = 1_790_000_000


def _trade(taker_side, yes_price, count="10.00", t="2026-09-21T00:00:00Z"):
    return {"ticker": "M", "taker_side": taker_side, "yes_price_dollars": f"{yes_price:.4f}",
            "no_price_dollars": f"{1 - yes_price:.4f}", "count_fp": count, "created_time": t,
            "is_block_trade": False}


def test_taker_buying_yes_means_maker_holds_no_at_no_price():
    f = maker_fill(_trade("yes", 0.30), result="no", event="E", category="Sports", close_ts=CLOSE)
    assert f.price == pytest.approx(0.70) and f.won
    assert f.pnl == pytest.approx(10 * 0.30 - fill_fee(0.70, 10))


def test_taker_buying_no_means_maker_holds_yes_at_yes_price():
    f = maker_fill(_trade("no", 0.30), result="no", event="E", category="Sports", close_ts=CLOSE)
    assert f.price == pytest.approx(0.30) and not f.won
    assert f.pnl == pytest.approx(-3.0 - fill_fee(0.30, 10))


def test_block_trades_and_bad_prices_are_ignored():
    blk = {**_trade("yes", 0.3), "is_block_trade": True}
    assert maker_fill(blk, "no", "E", "S", CLOSE) is None
    assert maker_fill(_trade("yes", 1.0), "no", "E", "S", CLOSE) is None


def test_late_flag_uses_two_hours_before_close():
    late = maker_fill(_trade("yes", 0.3, t="2026-09-21T13:00:00Z"), "no", "E", "S", CLOSE)
    early = maker_fill(_trade("yes", 0.3, t="2026-09-20T00:00:00Z"), "no", "E", "S", CLOSE)
    assert late.late and not early.late


def test_fill_fee_rounds_up_per_fill():
    assert fill_fee(0.5, 1) == 0.01
    assert fill_fee(0.5, 100) == 0.44


def test_clustered_roi_net_vs_gross():
    fills = [Fill("m", f"E{i}", "S", CLOSE, False, 0.5, 10, i % 2 == 0) for i in range(10)]
    gross, _ = clustered_roi(fills, net=False)
    net, se = clustered_roi(fills)
    assert gross == pytest.approx(0.0, abs=1e-9) and net < 0 and se > 0


def test_go_needs_significance_both_halves_and_size():
    row = lambda roi, se=0.01, m=300, e=80: {"markets": m, "events": e, "roi": roi, "se": se}
    seg = lambda r: {"overall": {"all": r}, "by_price": {}, "by_timing_price": {},
                     "by_category": {}, "by_category_price": {}}
    assert go_candidates(seg(row(0.05)), seg(row(0.04)), seg(row(0.06)))
    assert not go_candidates(seg(row(0.01, se=0.02)), seg(row(0.04)), seg(row(0.06)))
    assert not go_candidates(seg(row(0.05)), seg(row(-0.01)), seg(row(0.06)))
    assert not go_candidates(seg(row(0.05)), seg(row(0.04, m=100)), seg(row(0.06)))

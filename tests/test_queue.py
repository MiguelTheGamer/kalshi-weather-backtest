from kalshibot.queue import ORDER_SIZE, VirtualOrder, apply_trades, best_fav_bid

T0 = 1_790_000_000  # 2026-09-21T14:13:20Z


def _order(queue_ahead=100.0, fav="yes", price=0.99):
    return VirtualOrder("M", "Sports", fav, price, queue_ahead, T0, T0 + 3600)


def _trade(taker, yes_price, count, t="2026-09-21T14:20:00Z"):
    return {"taker_side": taker, "yes_price_dollars": f"{yes_price:.4f}",
            "no_price_dollars": f"{1 - yes_price:.4f}", "count_fp": str(count), "created_time": t}


def test_fills_only_after_queue_ahead_is_consumed():
    o = _order(queue_ahead=100)
    apply_trades(o, [_trade("no", 0.99, 60)])
    assert not o.filled and o.consumed == 60
    apply_trades(o, [_trade("no", 0.99, 50 + ORDER_SIZE, t="2026-09-21T14:30:00Z")])
    assert o.filled and not o.swept


def test_ignores_trades_on_wrong_side_price_or_before_entry():
    o = _order(queue_ahead=10)
    apply_trades(o, [_trade("yes", 0.99, 500),                               # favorite buyers
                     _trade("no", 0.99, 500, t="2026-09-21T14:00:00Z")])      # before we joined
    assert o.consumed == 0 and not o.filled


def test_trade_through_our_price_means_swept_and_filled():
    o = _order(queue_ahead=10_000)
    apply_trades(o, [_trade("no", 0.98, 1)])
    assert o.filled and o.swept


def test_no_side_favorite_uses_no_prices():
    o = _order(queue_ahead=5, fav="no", price=0.98)
    apply_trades(o, [_trade("yes", 0.02, 5 + ORDER_SIZE)])  # long-shot YES buyers at 2c
    assert o.filled


def test_best_fav_bid_picks_highest_price():
    book = {"orderbook_fp": {"yes_dollars": [["0.0100", "50"], ["0.9800", "20"], ["0.9900", "7"]],
                             "no_dollars": []}}
    assert best_fav_bid(book, "yes") == (0.99, 7.0)
    assert best_fav_bid(book, "no") is None

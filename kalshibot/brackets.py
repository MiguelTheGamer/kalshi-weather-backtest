"""Turn Kalshi temperature markets into integer-degree brackets, plus fees."""
import math
from dataclasses import dataclass

NEG_INF = -10_000
POS_INF = 10_000
FEE_RATE = 0.07  # Kalshi "quadratic" taker fee: ceil(0.07 * C * P * (1 - P))


@dataclass(frozen=True)
class Bracket:
    ticker: str
    lo: int  # inclusive, whole °F
    hi: int  # inclusive, whole °F
    yes_bid: float
    yes_ask: float
    label: str = ""

    def contains(self, temp: int) -> bool:
        return self.lo <= temp <= self.hi


def _price(m: dict, key: str) -> float:
    raw = m.get(key)
    return float(raw) if raw not in (None, "") else 0.0


def parse_bracket(m: dict) -> Bracket:
    """Map a market's strike fields to an inclusive [lo, hi] range.

    'between' floor=69 cap=70 -> 69..70; 'less' cap=69 -> ..68;
    'greater' floor=76 -> 77..
    """
    kind = m["strike_type"]
    floor, cap = m.get("floor_strike"), m.get("cap_strike")
    if kind == "between":
        lo, hi = int(floor), int(cap)
    elif kind == "less":
        lo, hi = NEG_INF, int(cap) - 1
    elif kind == "greater":
        lo, hi = int(floor) + 1, POS_INF
    else:
        raise ValueError(f"unknown strike_type {kind!r} on {m.get('ticker')}")
    return Bracket(
        ticker=m["ticker"],
        lo=lo,
        hi=hi,
        yes_bid=_price(m, "yes_bid_dollars"),
        yes_ask=_price(m, "yes_ask_dollars"),
        label=m.get("yes_sub_title", ""),
    )


def taker_fee(price: float, contracts: float = 1.0) -> float:
    """Total fee in dollars for `contracts` filled at `price`, rounded up to the cent."""
    raw = FEE_RATE * contracts * price * (1.0 - price)
    return math.ceil(round(raw * 100, 6)) / 100

"""Find mispriced brackets and size bets with fractional Kelly."""
import math
from dataclasses import dataclass

from .brackets import Bracket, taker_fee
from .config import RiskConfig


@dataclass(frozen=True)
class Trade:
    ticker: str
    side: str        # "yes" or "no"
    price: float     # what we pay per contract
    prob: float      # our probability this side wins
    edge: float      # expected profit per contract after fees
    contracts: int
    label: str = ""

    @property
    def cost(self) -> float:
        return self.contracts * self.price + taker_fee(self.price, self.contracts)


def _candidate(ticker: str, side: str, price: float, prob: float, label: str,
               cfg: RiskConfig) -> Trade | None:
    if not (cfg.min_price <= price <= cfg.max_price):
        return None
    fee = taker_fee(price)
    edge = prob - price - fee
    if not (cfg.min_edge <= edge <= cfg.max_edge):
        return None
    kelly = edge / (1.0 - price)
    stake = min(cfg.kelly_fraction * kelly * cfg.bankroll, cfg.max_stake_per_market)
    contracts = math.floor(stake / (price + fee))
    if contracts < 1:
        return None
    return Trade(ticker, side, price, prob, edge, contracts, label)


def find_trades(brackets: list[Bracket], probs: dict[str, float], cfg: RiskConfig) -> list[Trade]:
    """Best trades for one event (one city-day), highest edge first."""
    found = []
    for b in brackets:
        p = probs[b.ticker]
        if 0 < b.yes_ask < 1:
            t = _candidate(b.ticker, "yes", b.yes_ask, p, b.label, cfg)
            if t:
                found.append(t)
        if 0 < b.yes_bid < 1:
            t = _candidate(b.ticker, "no", round(1.0 - b.yes_bid, 4), 1.0 - p, b.label, cfg)
            if t:
                found.append(t)
    found.sort(key=lambda t: t.edge, reverse=True)
    return found[: cfg.max_trades_per_event]


def cap_daily(trades: list[Trade], already_staked: float, cfg: RiskConfig) -> list[Trade]:
    """Drop trades (lowest edge first) that would exceed the daily stake limit."""
    kept, total = [], already_staked
    for t in sorted(trades, key=lambda t: t.edge, reverse=True):
        if total + t.cost <= cfg.max_daily_stake:
            kept.append(t)
            total += t.cost
    return kept


def settle_pnl(side: str, price: float, contracts: int, yes_won: bool) -> float:
    """Net dollars from a held-to-settlement position, fees included."""
    won = yes_won if side == "yes" else not yes_won
    payout = contracts * 1.0 if won else 0.0
    return payout - contracts * price - taker_fee(price, contracts)

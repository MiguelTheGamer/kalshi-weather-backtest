"""Settings, read from environment variables (or a .env file next to the project)."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class RiskConfig:
    bankroll: float = 100.0          # dollars the bot may size against
    kelly_fraction: float = 0.25     # bet a quarter of full Kelly
    min_edge: float = 0.06           # required EV per contract after fees ($)
    max_edge: float = 0.40           # bigger "edges" usually mean the model is wrong
    min_price: float = 0.04          # skip lottery tickets...
    max_price: float = 0.94          # ...and picking up pennies in front of a steamroller
    max_stake_per_market: float = 5.0
    max_daily_stake: float = 25.0
    max_trades_per_event: int = 2

    @classmethod
    def from_env(cls) -> "RiskConfig":
        d = cls()
        return cls(
            bankroll=_f("BANKROLL", d.bankroll),
            kelly_fraction=_f("KELLY_FRACTION", d.kelly_fraction),
            min_edge=_f("MIN_EDGE", d.min_edge),
            max_edge=_f("MAX_EDGE", d.max_edge),
            min_price=_f("MIN_PRICE", d.min_price),
            max_price=_f("MAX_PRICE", d.max_price),
            max_stake_per_market=_f("MAX_STAKE_PER_MARKET", d.max_stake_per_market),
            max_daily_stake=_f("MAX_DAILY_STAKE", d.max_daily_stake),
            max_trades_per_event=int(_f("MAX_TRADES_PER_EVENT", d.max_trades_per_event)),
        )

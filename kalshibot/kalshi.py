"""Kalshi REST client: public market data plus signed portfolio/order calls.

Signing (per Kalshi docs): base64(RSA-PSS-SHA256(timestamp_ms + METHOD + path)),
where path includes the /trade-api/v2 prefix but not the query string.
"""
import base64
import hashlib
import json
import os
import time
import urllib.parse
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .config import DATA_DIR
from .http import get_json, request_json

CACHE_DIR = DATA_DIR / "cache"

MARKET_DATA_URL = "https://api.elections.kalshi.com/trade-api/v2"
TRADING_URLS = {
    "demo": "https://external-api.demo.kalshi.co/trade-api/v2",
    "live": "https://external-api.kalshi.com/trade-api/v2",
}


# ---------- public market data (no auth) ----------

def list_markets(**params) -> list[dict]:
    out, cursor = [], None
    while True:
        q = {"limit": 1000, **params}
        if cursor:
            q["cursor"] = cursor
        page = get_json(f"{MARKET_DATA_URL}/markets", q)
        out.extend(page.get("markets", []))
        cursor = page.get("cursor")
        if not cursor:
            return out


def list_events(series_ticker: str, status: str, max_events: int = 400) -> list[dict]:
    out, cursor = [], None
    while len(out) < max_events:
        q = {"series_ticker": series_ticker, "status": status, "limit": 200, "with_nested_markets": "true"}
        if cursor:
            q["cursor"] = cursor
        page = get_json(f"{MARKET_DATA_URL}/events", q)
        out.extend(page.get("events", []))
        cursor = page.get("cursor")
        if not cursor:
            break
    return out[:max_events]


def batch_candles(tickers: list[str], start_ts: int, end_ts: int, period_min: int = 60,
                  cache: bool = False) -> dict[str, list[dict]]:
    """Hourly bid/ask candles. With cache=True (settled markets only) results are kept on disk."""
    key = hashlib.sha1(f"{sorted(tickers)}{start_ts}{end_ts}{period_min}".encode()).hexdigest()
    path = CACHE_DIR / f"candles_{key}.json"
    if cache and path.exists():
        return json.loads(path.read_text())
    out = _fetch_candles(tickers, start_ts, end_ts, period_min)
    if cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
    return out


def _fetch_candles(tickers: list[str], start_ts: int, end_ts: int, period_min: int) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for i in range(0, len(tickers), 20):
        chunk = tickers[i:i + 20]
        page = get_json(f"{MARKET_DATA_URL}/markets/candlesticks", {
            "market_tickers": ",".join(chunk), "start_ts": start_ts, "end_ts": end_ts,
            "period_interval": period_min,
        })
        for m in page.get("markets", []):
            out[m.get("market_ticker") or m.get("ticker")] = m.get("candlesticks", [])
    return out


# ---------- authenticated trading ----------

class KalshiTrader:
    def __init__(self, env: str, key_id: str, private_key_path: str):
        if env not in TRADING_URLS:
            raise ValueError(f"env must be one of {list(TRADING_URLS)}")
        self.env = env
        self.base = TRADING_URLS[env]
        self.key_id = key_id
        pem = Path(private_key_path).expanduser().read_bytes()
        self._key = serialization.load_pem_private_key(pem, password=None)

    @classmethod
    def from_env(cls) -> "KalshiTrader":
        env = os.environ.get("KALSHI_ENV", "demo")
        key_id = os.environ.get("KALSHI_KEY_ID")
        key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
        if not key_id or not key_path:
            raise RuntimeError("Set KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH in .env")
        return cls(env, key_id, key_path)

    def _sign(self, ts_ms: str, method: str, path: str) -> str:
        msg = f"{ts_ms}{method}{path}".encode()
        sig = self._key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode()

    def _call(self, method: str, endpoint: str, params: dict | None = None, body: dict | None = None) -> dict:
        url = f"{self.base}{endpoint}"
        path = urllib.parse.urlparse(url).path
        ts = str(int(time.time() * 1000))
        headers = {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": self._sign(ts, method, path),
        }
        return request_json(method, url, params=params, body=body, headers=headers)

    def balance(self) -> float:
        data = self._call("GET", "/portfolio/balance")
        if "balance_dollars" in data:
            return float(data["balance_dollars"])
        return data.get("balance", 0) / 100

    def positions(self) -> dict:
        return self._call("GET", "/portfolio/positions", params={"limit": 1000})

    def buy(self, ticker: str, side: str, price: float, count: float) -> dict:
        """Buy YES or NO with an immediate-or-cancel limit order (never rests on the book).

        The V2 endpoint has one YES book: buying YES is a bid at `price`,
        buying NO at `price` is an ask (sell YES) at 1 - price.
        """
        if side not in ("yes", "no"):
            raise ValueError("side must be 'yes' or 'no'")
        if not (0.01 <= price <= 0.99) or count <= 0:
            raise ValueError(f"bad order price={price} count={count}")
        book_side, book_price = ("bid", price) if side == "yes" else ("ask", 1.0 - price)
        body = {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "side": book_side,
            "count": f"{count:.2f}",
            "price": f"{book_price:.4f}",
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
        }
        return self._call("POST", "/portfolio/events/orders", body=body)

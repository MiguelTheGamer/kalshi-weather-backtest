# Kalshi weather bot: research findings (2026-10-11)

**Bottom line: no tradeable edge found. Do not trade real money with this code.**

## What was tested
Daily-high temperature markets (the highest-volume weather series), walk-forward
backtest on settled events from May–Oct 2026, about 48 days per city. Fills were
assumed at the ask, Kalshi's 7% quadratic taker fee was included, and calibration
was fit only on earlier days. Accuracy is scored by log loss on the winning bracket
(lower is better).

| Approach | Result |
|---|---|
| Forecast model (GFS+ECMWF+ICON consensus, calibrated bias/sigma), day before | Market more accurate (NYC 0.95 vs model 1.22). Lost 14% of stake. |
| Same model, morning of | Market 0.81 vs model 1.06. Lost 9%. |
| Model + live airport obs (11am–5pm local) | Market more accurate at every hour in NYC, CHI, MIA. Lost money overall. |
| Blend of model and market prices | Blend never beat the market alone. |
| Bracket arbitrage (buy/sell every bracket) | None. Best of 38 open events was -5¢ after fees. |

A few individual cells were profitable (e.g. Chicago 3pm +$78 on $113). Out of
24 city×hour×strategy tests that is what noise looks like; it is not evidence.

## Useful facts learned
- Weather markets now settle on **The Weather Company** (weather.com/kalshi), not the NWS CLI report.
- The official high is usually **+1°F above the max hourly METAR** (0: 39%, +1: 52%, +2: 7% of 204 station-days).
- By ~5pm local the market already prices the answer (log loss ~0.03).
- Order API is V2: `POST /portfolio/events/orders`, string prices/counts, `side` bid/ask on the YES book.

## Code
- `kalshibot/backtest.py`: walk-forward forecast backtest + calibration
- `kalshibot/research.py`: intraday/blend study (`intraday_study`)
- `kalshibot/kalshi.py`: market data + signed trading client. **The trading client is untested** and has never been run against demo or live.

Re-run, e.g.:
```
.venv\Scripts\python -c "from datetime import date; from kalshibot.research import intraday_study; from kalshibot.config import RiskConfig; print({h:s.summary() for h,s in intraday_study('KXHIGHNY', date(2026,5,1), (13,15), RiskConfig()).items()})"
```

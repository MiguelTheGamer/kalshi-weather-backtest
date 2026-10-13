# Can free weather data beat Kalshi's temperature markets?

**Short answer: no.** I built a forecasting model and trading backtest for Kalshi's
daily-high temperature markets. The market's own prices predicted the outcome
better than my model at every decision time, in every city tested. This repo is
the pipeline and the negative result.

## The question

[Kalshi](https://kalshi.com) lists daily markets on each US city's high
temperature, split into roughly six 2°F brackets. Each bracket pays $1 if the
official high lands in it. Weather looked like the most promising category to
find an edge:

- The inputs are free: global forecast models, plus live airport observations.
- Outcomes are objective and settle within a day.
- Volume is real: LA, NYC, Chicago and Miami trade $100k+ per day.

If a calibrated model could produce better probabilities than the market,
buying underpriced brackets would be profitable even after fees.

## Method

**Forecast.** For each settlement station I take the consensus of three model
families (GFS, ECMWF IFS, ICON) via Open-Meteo. I compute each model's maximum
temperature over the *climate day*, which runs midnight to midnight local
standard time all year, and average the three.

**Calibration.** The residual (actual minus forecast) is fit as Normal(bias, σ).
It uses a rolling 60-day window of *earlier* days only, so nothing peeks at the
future. Bracket probabilities come from integrating that distribution over
each bracket's whole-degree range.

**Intraday variant.** Final high = max(highest temperature reported so far,
max over the remaining hours). The "so far" part comes from archived METAR
airport reports, using the 0.1°C precision temperature group when present. The
remaining hours use the forecast for hours after the decision time.

**Backtest.** Walk-forward over settled Kalshi events (May–Oct 2026, about 48
days per city). Prices are Kalshi's hourly bid/ask candles at the decision
moment. Fills are assumed at the ask, Kalshi's quadratic taker fee
(7% × P × (1−P)) is charged, and sizing uses quarter-Kelly with per-market
caps. Forecasts are leak-free:

| Decision time | Forecast used |
|---|---|
| 3pm the day before | model runs issued ≥48h before each hour |
| 8am the day of | runs issued ≥24h before each hour |
| 11am–5pm the day of | same, plus observations up to that moment |

**Scoring.** Accuracy is measured by log loss on the winning bracket (lower is
better) for the market's mid prices and for each model. Trading P&L is
reported alongside.

## Results

### Forecast model vs. market (New York City)

| Decision time | Market log loss | Model log loss | Simulated P&L |
|---|---|---|---|
| Day before, 3pm | **0.95** | 1.22 | −14% of stake |
| Day of, 8am | **0.81** | 1.06 | −9% |

### With live observations (log loss, lower is better)

| City | 11am | 1pm | 3pm | 5pm |
|---|---|---|---|---|
| NYC market / model | **0.73** / 1.00 | **0.41** / 0.68 | **0.33** / 0.82 | **0.03** / 0.65 |
| Chicago market / model | **1.00** / 1.36 | **0.62** / 0.95 | **0.19** / 0.76 | **0.03** / 0.50 |
| Miami market / model | **0.84** / 1.23 | **0.44** / 0.90 | **0.24** / 0.74 | **0.12** / 0.63 |

The market won every cell. Geometric blends of model and market at weights
0.25, 0.5 and 0.75 never beat the market alone either.

### Arbitrage

I scanned all 38 open city-day events for risk-free combinations: buying every
bracket's YES for less than $1, or selling every bracket for more than $1.
After fees, the best combination returned **−5¢**.

### On the profitable-looking cells

A few configurations made money in simulation, for example Chicago at 3pm:
+$78 on $113 staked. That is 4 winners out of 24 city × hour × strategy tests
on about 48 days each, which is the pattern noise produces, so I don't treat
it as evidence. The accuracy comparison, which doesn't depend on a handful of
trades, points the same way in every cell.

## Follow-up: is there a favorite-longshot bias to exploit?

[Research on Kalshi](https://cepr.org/voxeu/columns/economics-kalshi-prediction-market)
finds a favorite-longshot bias: cheap contracts lose their buyers money. So I
tested the obvious trade, buying NO on longshots, across all non-parlay
markets. The sample was about 11,000 snapshots from settled markets priced
24h and 2h before close (`python -m kalshibot longshot`).

| Strategy | Result |
|---|---|
| Buy NO on longshots at the market price | Lost 2–8% after fees in every price bucket, in both halves of the sample |
| Market calibration at the mid price | Well calibrated (e.g. 10–20¢ contracts won 16–18% of the time) |
| Post orders and wait for a fill | Looks profitable, but only because every order is assumed to fill, so it's an upper bound |

The longshot buyers' losses mostly go to the spread and fees, not to anyone
betting against them. I also wrote the go/no-go rule before running the test,
and it turned out to be underspecified. The details, including how I handled
that, are in [FINDINGS.md](FINDINGS.md).

## Follow-up: what do market makers actually earn?

Every Kalshi trade records which side was the taker, so the maker's realized
P&L can be computed directly, with no fill assumptions and with adverse
selection included. The sample was 1,500 settled markets (about 950k fills and
65M contracts), with maker fees charged on every fill
(`python -m kalshibot makers`).

| Segment | Net return per $ |
|---|---|
| All maker fills | −0.9% ± 3.1% (break-even) |
| Sports / crypto / commodities | −1.2% / −0.2% / +0.2% |
| **Buying the 97–99¢ side** (selling 1–3¢ long shots) | **+0.9% ± 0.2%**, +1.2% in sports, positive in both halves |

The only real edge is collecting the favorite-longshot bias: posting orders on
near-certain outcomes, mostly in the final two hours of sports games. It's thin
(~1¢ per win against ~98¢ per loss), and it belongs to makers already at the
front of the queue. `python -m kalshibot queue` paper-simulates a new order
joining the back of the real queue to measure whether it would ever fill; a
live run during NFL, MLB, NBA and NHL games is in progress.

## What I learned

1. **These markets are efficient against public data.** Other participants
   are clearly running the same free models, and by about 5pm local the price
   has converged on the answer.
2. **Settlement details matter more than the model.** Markets now settle on
   The Weather Company's reading, not the NWS climate report. The official high
   is usually **0–2°F above the highest hourly airport report**, because the
   true peak falls between hourly reports. Across 204 station-days in NYC,
   Chicago and Miami, the official value was equal 39% of the time, +1°F 52%,
   and +2°F 7%. Ignoring that gap makes an intraday model confidently wrong.
3. **A negative result needs the same rigor as a positive one.** Leak-free
   forecasts, calibration fit only on earlier days, real historical prices and
   real fees are what make "no edge" a trustworthy conclusion rather than a bug.

## Running it

```bash
py -3.14 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m kalshibot backtest --since 2026-05-01
.venv\Scripts\python -m kalshibot intraday --since 2026-05-01 --hours 11 13 15 17
.venv\Scripts\python -m pytest --cov=kalshibot
```

Tests cover the pricing math: bracket parsing, fees, the probability model,
sizing and settlement, plus climate-day windows and METAR parsing. That code
is near 100% covered. The network-fetching code is exercised by the backtests
rather than by unit tests, so total coverage is about 47%.

No API keys are needed: Kalshi market data, Open-Meteo and the Iowa
Environmental Mesonet METAR archive are all public. Results are written to
`data/`.

| File | What it does |
|---|---|
| `kalshibot/backtest.py` | Walk-forward forecast backtest and rolling calibration |
| `kalshibot/research.py` | Intraday, observation and blend study |
| `kalshibot/model.py` | Bracket probabilities, including the observation offset |
| `kalshibot/weather.py` | Open-Meteo forecasts, METAR parsing, climate-day windows |
| `kalshibot/strategy.py` | Edge after fees, quarter-Kelly sizing, settlement P&L |
| `kalshibot/kalshi.py` | Market data client and an RSA-PSS signed order client* |

\*The order client was written against Kalshi's V2 order API but has **never
been run**, on demo or live. Nothing in this repo places real trades.

## Limitations

- About 48 scored days per city, all from May to October. Winter weather such
  as fronts and snow is untested.
- Fills are assumed at the quoted ask with no queue position or depth, which
  is optimistic for the model, and it still lost.
- The forecasts are three free deterministic models, not full ensembles or
  paid data.

Data: [Kalshi API](https://docs.kalshi.com), [Open-Meteo](https://open-meteo.com),
[Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu).

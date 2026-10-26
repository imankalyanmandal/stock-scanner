# Strategy experiments (2026-10-26)

496 stocks, data period 10y. Same prices for every variant.

## Swing variants (2017-11-13 to 2026-10-23)

Expectancy is average R per trade after costs. **1st / 2nd half** splits the period in two: a real improvement should show up in both.

| Variant | Trades | Win % | Avg win R | Avg loss R | Expectancy R | 1st half | 2nd half | Profit factor | Return % (1% risk) | Max DD % |
|---|---|---|---|---|---|---|---|---|---|---|
| A_large_mid_only | 228 | 46.9 | 0.95 | -0.76 | **0.047** | 0.028 (106) | 0.063 (122) | 1.12 | 10.1 | -11.3 |
| B_with_smallcaps | 259 | 47.5 | 0.98 | -0.75 | **0.072** | 0.155 (106) | 0.015 (153) | 1.18 | 16.1 | -12.3 |
| C_smallcaps_only | 58 | 43.1 | 1.13 | -0.76 | **0.054** | 0.413 (15) | -0.072 (43) | 1.12 | 4.2 | -7.4 |

Variant settings:

- **A_large_mid_only**: `{"universe": {"indexes": ["nifty50", "niftynext50", "niftymidcap150"]}}`
- **B_with_smallcaps**: `{}`
- **C_smallcaps_only**: `{"universe": {"indexes": ["niftysmallcap250"]}}`

## Monthly momentum rotation (2017-12-01 to 2026-10-23)

| Strategy | CAGR % | Volatility % | Max drawdown % | Worst year % | Return / vol | Months in cash % | Avg turnover % |
|---|---|---|---|---|---|---|---|
| M_large_mid_only | **26.2** | 16.3 | -21.5 | -13.0 | 1.6 | 27.1 | 41.3 |
| M_with_smallcaps | **25.9** | 17.6 | -20.6 | -10.3 | 1.47 | 27.1 | 48.4 |
| M_smallcaps_only | **10.7** | 19.6 | -49.4 | -24.5 | 0.54 | 27.1 | 46.6 |
| B2_blend_with_smallcaps | **19.6** | 13.4 | -16.2 | -4.0 | 1.46 | 0.0 | 30.3 |
| nifty50 (buy & hold) | **9.5** | 16.8 | -38.4 | -13.5 | 0.56 | – | – |
| nifty500 (buy & hold) | **10.2** | 16.7 | -38.3 | -10.2 | 0.61 | – | – |

Calendar-year returns (%):

| | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|
| M_large_mid_only | 7.2 | -13.0 | 8.1 | 37.7 | 90.2 | 13.8 | 55.1 | 40.6 | 17.3 | 2.7 |
| M_with_smallcaps | 7.4 | -10.3 | 6.1 | 42.4 | 101.4 | 9.3 | 56.0 | 38.8 | 8.9 | 2.3 |
| M_smallcaps_only | 8.3 | -21.4 | -24.5 | 31.1 | 36.3 | -11.3 | 55.4 | 46.3 | 6.9 | -0.3 |
| B2_blend_with_smallcaps | 5.5 | -1.1 | 6.6 | 37.2 | 58.6 | 7.5 | 44.2 | 23.0 | 11.2 | -4.0 |
| nifty50 B&H | 4.0 | 3.2 | 12.0 | 14.9 | 24.1 | 4.3 | 20.0 | 8.8 | 10.5 | -13.5 |
| nifty500 B&H | 4.7 | -3.4 | 7.7 | 16.7 | 30.2 | 3.0 | 25.8 | 15.2 | 6.7 | -10.2 |

Variant settings:

- **M_large_mid_only**: `{"universe": {"indexes": ["nifty50", "niftynext50", "niftymidcap150"]}}`
- **M_with_smallcaps**: `{}`
- **M_smallcaps_only**: `{"universe": {"indexes": ["niftysmallcap250"]}}`
- **B2_blend_with_smallcaps**: `{"momentum": {"sleeves": [{"score": "momentum", "weight": 0.5}, {"score": "low_vol", "weight": 0.5, "regime_filter": false}]}}`

## Read with care

- **Survivorship bias:** every variant uses today's index members for the whole period, which flatters results, more so the further back the test goes.
- **Picking the best of many variants overfits.** Prefer a variant that wins in both halves and for a reason you can explain.
- Swing results: earnings, delivery % and the exclusion list are not applied historically.
- Momentum results: costs and cash yield included; taxes are not.

# Strategy experiments (2026-10-26)

249 stocks, data period 10y. Same prices for every variant.

## Swing variants (2017-11-13 to 2026-10-26)

Expectancy is average R per trade after costs. **1st / 2nd half** splits the period in two: a real improvement should show up in both.

| Variant | Trades | Win % | Avg win R | Avg loss R | Expectancy R | 1st half | 2nd half | Profit factor | Return % (1% risk) | Max DD % |
|---|---|---|---|---|---|---|---|---|---|---|
| B_baseline_live | 228 | 46.9 | 0.95 | -0.76 | **0.047** | 0.028 (106) | 0.063 (122) | 1.12 | 10.1 | -11.3 |
| D_momentum_top40 | 178 | 49.4 | 0.92 | -0.78 | **0.059** | 0.045 (88) | 0.073 (90) | 1.15 | 11.9 | -7.0 |
| I1_macd_bullish | 187 | 48.7 | 1.0 | -0.74 | **0.107** | 0.051 (85) | 0.153 (102) | 1.28 | 19.5 | -5.1 |
| I2_supertrend_up | 227 | 46.7 | 0.93 | -0.76 | **0.031** | 0.028 (106) | 0.033 (121) | 1.08 | 6.4 | -11.5 |
| I3_adx_20 | 183 | 45.4 | 0.92 | -0.76 | **0.002** | 0.015 (83) | -0.009 (100) | 1.0 | 3.9 | -12.8 |
| I4_adx_25 | 142 | 50.0 | 0.92 | -0.77 | **0.077** | 0.028 (66) | 0.12 (76) | 1.2 | 10.8 | -9.5 |
| I5_bb_squeeze | 129 | 45.0 | 1.06 | -0.75 | **0.065** | 0.08 (60) | 0.051 (69) | 1.16 | 7.9 | -6.9 |
| I6_all_four | 67 | 41.8 | 1.22 | -0.78 | **0.056** | 0.051 (30) | 0.059 (37) | 1.12 | 4.9 | -5.7 |
| I7_top40_plus_squeeze | 106 | 49.1 | 1.0 | -0.77 | **0.101** | 0.173 (52) | 0.031 (54) | 1.26 | 9.6 | -5.4 |

Variant settings:

- **B_baseline_live**: `{}`
- **D_momentum_top40**: `{"rs": {"momentum_top_n": 40}}`
- **I1_macd_bullish**: `{"indicators": {"require_macd_bullish": true}}`
- **I2_supertrend_up**: `{"indicators": {"require_supertrend_up": true}}`
- **I3_adx_20**: `{"indicators": {"min_adx": 20}}`
- **I4_adx_25**: `{"indicators": {"min_adx": 25}}`
- **I5_bb_squeeze**: `{"indicators": {"require_bb_squeeze": true}}`
- **I6_all_four**: `{"indicators": {"require_macd_bullish": true, "require_supertrend_up": true, "min_adx": 20, "require_bb_squeeze": true}}`
- **I7_top40_plus_squeeze**: `{"rs": {"momentum_top_n": 40}, "indicators": {"require_bb_squeeze": true}}`

## Monthly momentum rotation (2017-12-01 to 2026-10-26)

| Strategy | CAGR % | Volatility % | Max drawdown % | Worst year % | Return / vol | Months in cash % | Avg turnover % |
|---|---|---|---|---|---|---|---|
| M2_top30_200dma_filter | **25.9** | 16.3 | -21.5 | -13.8 | 1.59 | 27.1 | 41.4 |
| nifty50 (buy & hold) | **9.5** | 16.8 | -38.4 | -12.9 | 0.57 | – | – |
| nifty500 (buy & hold) | **10.3** | 16.7 | -38.3 | -9.5 | 0.61 | – | – |

Calendar-year returns (%):

| | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|
| M2_top30_200dma_filter | 7.7 | -13.8 | 8.4 | 38.4 | 91.3 | 11.6 | 56.8 | 41.5 | 13.6 | 3.6 |
| nifty50 B&H | 4.0 | 3.2 | 12.0 | 14.9 | 24.1 | 4.3 | 20.0 | 8.8 | 10.5 | -12.9 |
| nifty500 B&H | 4.7 | -3.4 | 7.7 | 16.7 | 30.2 | 3.0 | 25.8 | 15.2 | 6.7 | -9.5 |

Variant settings:

- **M2_top30_200dma_filter**: `{}`

## Read with care

- **Survivorship bias:** every variant uses today's index members for the whole period, which flatters results, more so the further back the test goes.
- **Picking the best of many variants overfits.** Prefer a variant that wins in both halves and for a reason you can explain.
- Swing results: earnings, delivery % and the exclusion list are not applied historically.
- Momentum results: costs and cash yield included; taxes are not.

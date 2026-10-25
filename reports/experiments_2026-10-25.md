# Strategy experiments (2026-10-25)

249 stocks, data period 10y. Same prices for every variant.

## Swing variants (2017-11-09 to 2026-10-23)

Expectancy is average R per trade after costs. **1st / 2nd half** splits the period in two: a real improvement should show up in both.

| Variant | Trades | Win % | Avg win R | Avg loss R | Expectancy R | 1st half | 2nd half | Profit factor | Return % (1% risk) | Max DD % |
|---|---|---|---|---|---|---|---|---|---|---|
| A_old_with_pullbacks | 296 | 46.3 | 0.92 | -0.77 | **0.014** | -0.0 (139) | 0.027 (157) | 1.03 | 5.5 | -16.3 |
| B_breakouts_only | 231 | 46.8 | 0.95 | -0.75 | **0.048** | 0.012 (108) | 0.079 (123) | 1.12 | 9.9 | -12.3 |
| C_undercut_filter | 179 | 45.8 | 1.0 | -0.77 | **0.041** | -0.037 (81) | 0.106 (98) | 1.1 | 8.4 | -11.3 |
| D_momentum_top40 | 180 | 48.9 | 0.92 | -0.78 | **0.049** | 0.026 (90) | 0.073 (90) | 1.12 | 10.6 | -7.3 |
| E_time_stop_12 | 221 | 42.1 | 1.21 | -0.83 | **0.025** | 0.108 (103) | -0.048 (118) | 1.05 | 9.7 | -10.5 |
| F_no_time_stop | 204 | 42.2 | 1.6 | -1.01 | **0.091** | 0.199 (98) | -0.009 (106) | 1.16 | 16.0 | -12.0 |
| G_partial_1_5R | 231 | 47.6 | 0.87 | -0.75 | **0.022** | -0.011 (108) | 0.052 (123) | 1.06 | 6.0 | -13.9 |
| H_C_D_E_combined | 129 | 46.5 | 1.22 | -0.91 | **0.08** | 0.134 (62) | 0.029 (67) | 1.16 | 16.0 | -6.0 |

Variant settings:

- **A_old_with_pullbacks**: `{"setups": {"pullback": {"enabled": true}}}`
- **B_breakouts_only**: `{}`
- **C_undercut_filter**: `{"setups": {"breakout": {"reject_undercut_base": true}}}`
- **D_momentum_top40**: `{"rs": {"momentum_top_n": 40}}`
- **E_time_stop_12**: `{"exits": {"time_stop_days": 12}}`
- **F_no_time_stop**: `{"exits": {"time_stop_days": 999}}`
- **G_partial_1_5R**: `{"exits": {"partial_r": 1.5}}`
- **H_C_D_E_combined**: `{"setups": {"breakout": {"reject_undercut_base": true}}, "rs": {"momentum_top_n": 40}, "exits": {"time_stop_days": 12}}`

## Monthly momentum rotation (2017-12-01 to 2026-10-23)

| Strategy | CAGR % | Volatility % | Max drawdown % | Worst year % | Return / vol | Months in cash % | Avg turnover % |
|---|---|---|---|---|---|---|---|
| M1_top30_always_invested | **25.6** | 20.7 | -36.7 | -11.5 | 1.23 | 0.0 | 37.9 |
| M2_top30_200dma_filter | **24.9** | 16.3 | -21.5 | -13.8 | 1.53 | 27.1 | 41.7 |
| M3_top20_200dma_filter | **26.3** | 17.3 | -23.7 | -14.6 | 1.52 | 27.1 | 46.5 |
| nifty50 (buy & hold) | **9.5** | 16.8 | -38.4 | -13.5 | 0.56 | – | – |
| nifty500 (buy & hold) | **10.2** | 16.7 | -38.3 | -10.2 | 0.61 | – | – |

Calendar-year returns (%):

| | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|
| M1_top30_always_invested | 7.7 | -3.3 | 13.4 | 38.0 | 83.1 | 21.9 | 65.3 | 38.3 | 2.9 | -11.5 |
| M2_top30_200dma_filter | 7.7 | -13.8 | 8.0 | 38.1 | 83.1 | 12.9 | 55.2 | 38.3 | 13.6 | 3.5 |
| M3_top20_200dma_filter | 7.1 | -14.6 | 3.2 | 44.9 | 105.6 | 19.9 | 53.9 | 34.4 | 12.2 | 2.2 |
| nifty50 B&H | 4.0 | 3.2 | 12.0 | 14.9 | 24.1 | 4.3 | 20.0 | 8.8 | 10.5 | -13.5 |
| nifty500 B&H | 4.7 | -3.4 | 7.7 | 16.7 | 30.2 | 3.0 | 25.8 | 15.2 | 6.7 | -10.2 |

Variant settings:

- **M1_top30_always_invested**: `{"momentum": {"regime_filter": false}}`
- **M2_top30_200dma_filter**: `{}`
- **M3_top20_200dma_filter**: `{"momentum": {"top_n": 20, "exit_rank": 30}}`

## Read with care

- **Survivorship bias:** every variant uses today's index members for the whole period, which flatters results, more so the further back the test goes.
- **Picking the best of many variants overfits.** Prefer a variant that wins in both halves and for a reason you can explain.
- Swing results: earnings, delivery % and the exclusion list are not applied historically.
- Momentum results: costs and cash yield included; taxes are not.

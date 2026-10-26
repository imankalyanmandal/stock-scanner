# Strategy experiments (2026-10-26)

249 stocks, data period 10y. Same prices for every variant.

## Monthly momentum rotation (2017-12-01 to 2026-10-23)

| Strategy | CAGR % | Volatility % | Max drawdown % | Worst year % | Return / vol | Months in cash % | Avg turnover % |
|---|---|---|---|---|---|---|---|
| M1_momentum_always_invested | **27.1** | 20.7 | -36.3 | -12.1 | 1.31 | 0.0 | 37.6 |
| M2_momentum_200dma_filter | **25.9** | 16.3 | -21.6 | -13.8 | 1.59 | 27.1 | 41.4 |
| L1_lowvol_always_invested | **12.8** | 13.5 | -27.6 | -12.0 | 0.95 | 0.0 | 11.6 |
| L2_lowvol_200dma_filter | **9.7** | 9.7 | -14.1 | -5.0 | 0.99 | 27.1 | 25.1 |
| B1_50_50_sleeves_both_filtered | **17.7** | 12.1 | -16.4 | -9.5 | 1.46 | 27.1 | 33.2 |
| B2_50_50_sleeves_lowvol_always_in | **19.5** | 12.9 | -16.7 | -4.4 | 1.52 | 0.0 | 26.3 |
| B3_blend_score_top30 | **16.9** | 12.5 | -16.1 | -9.4 | 1.35 | 27.1 | 38.5 |
| B4_blend_score_always_invested | **17.6** | 16.6 | -30.0 | -17.0 | 1.06 | 0.0 | 33.4 |
| nifty50 (buy & hold) | **9.5** | 16.8 | -38.4 | -13.5 | 0.56 | – | – |
| nifty500 (buy & hold) | **10.2** | 16.7 | -38.3 | -10.2 | 0.61 | – | – |

Calendar-year returns (%):

| | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|
| M1_momentum_always_invested | 7.7 | -3.3 | 13.1 | 40.8 | 91.3 | 22.5 | 69.1 | 41.4 | 3.3 | -12.1 |
| M2_momentum_200dma_filter | 7.7 | -13.8 | 8.4 | 38.3 | 91.3 | 11.6 | 56.8 | 41.4 | 13.6 | 3.5 |
| L1_lowvol_always_invested | 3.5 | 6.6 | 6.2 | 26.0 | 25.1 | 5.9 | 33.6 | 11.7 | 14.0 | -12.0 |
| L2_lowvol_200dma_filter | 3.5 | -5.0 | 1.0 | 20.8 | 24.5 | -1.5 | 26.7 | 10.0 | 11.0 | -0.3 |
| B1_50_50_sleeves_both_filtered | 5.6 | -9.5 | 4.7 | 29.4 | 55.0 | 5.0 | 41.3 | 25.1 | 12.4 | 1.6 |
| B2_50_50_sleeves_lowvol_always_in | 5.6 | -4.0 | 7.4 | 32.9 | 55.4 | 9.0 | 45.2 | 26.1 | 13.9 | -4.4 |
| B3_blend_score_top30 | 4.5 | -9.4 | 2.5 | 28.4 | 48.2 | 3.5 | 43.1 | 31.4 | 10.9 | 0.5 |
| B4_blend_score_always_invested | 4.5 | 5.1 | 12.2 | 37.5 | 46.9 | 5.4 | 53.2 | 31.4 | -3.5 | -17.0 |
| nifty50 B&H | 4.0 | 3.2 | 12.0 | 14.9 | 24.1 | 4.3 | 20.0 | 8.8 | 10.5 | -13.5 |
| nifty500 B&H | 4.7 | -3.4 | 7.7 | 16.7 | 30.2 | 3.0 | 25.8 | 15.2 | 6.7 | -10.2 |

Variant settings:

- **M1_momentum_always_invested**: `{"momentum": {"regime_filter": false}}`
- **M2_momentum_200dma_filter**: `{}`
- **L1_lowvol_always_invested**: `{"momentum": {"score": "low_vol", "regime_filter": false}}`
- **L2_lowvol_200dma_filter**: `{"momentum": {"score": "low_vol"}}`
- **B1_50_50_sleeves_both_filtered**: `{"momentum": {"sleeves": [{"score": "momentum", "weight": 0.5}, {"score": "low_vol", "weight": 0.5}]}}`
- **B2_50_50_sleeves_lowvol_always_in**: `{"momentum": {"sleeves": [{"score": "momentum", "weight": 0.5}, {"score": "low_vol", "weight": 0.5, "regime_filter": false}]}}`
- **B3_blend_score_top30**: `{"momentum": {"score": "blend"}}`
- **B4_blend_score_always_invested**: `{"momentum": {"score": "blend", "regime_filter": false}}`

## Read with care

- **Survivorship bias:** every variant uses today's index members for the whole period, which flatters results, more so the further back the test goes.
- **Picking the best of many variants overfits.** Prefer a variant that wins in both halves and for a reason you can explain.
- Swing results: earnings, delivery % and the exclusion list are not applied historically.
- Momentum results: costs and cash yield included; taxes are not.

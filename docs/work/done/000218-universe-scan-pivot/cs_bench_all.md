# Universe-Scan Strategies — 5y Bench (Phase 1 verification)

- Period: 2020-01-01 .. 2025-12-31
- KRX universe: KOSPI top-200 + KOSDAQ top-150 (cached, current Marcap pin)
- Crypto universe: Binance USDT spot top-30 (cached, current 24h volume pin)
- Cost: KRX 55bp / Crypto 16bp round-trip

## Results

| Strategy | Sharpe | MDD | Ann.Return | Avg Holdings | Trades | Exposure |
|----------|-------:|----:|-----------:|-------------:|-------:|---------:|
| cs_tsmom_kr_daily | 1.048 | -46.35% | 35.14% | 20.0 | 287 | 99.6% |
| cs_rsi_div_kr | 0.970 | -35.46% | 24.58% | 20.0 | 294 | 100.0% |
| cs_bb_macd_kr | -0.323 | -76.60% | -13.43% | 5.0 | 268 | 73.5% |
| cs_adx_ma_kr | 1.031 | -45.36% | 33.55% | 19.5 | 295 | 100.0% |
| cs_tsmom_crypto_daily | 1.019 | -77.01% | 62.60% | 7.7 | 242 | 88.4% |
| cs_rsi_div_crypto | 1.015 | -81.66% | 62.82% | 9.2 | 392 | 99.5% |
| cs_macd_vol_crypto | 1.012 | -84.51% | 68.73% | 6.8 | 396 | 90.0% |
# Cross-Sectional TSMOM 12-1 — Binance Crypto Universe

- Universe: top-30 USDT spot pairs by 24h quote volume (stablecoins, wrapped, leveraged 제외)
  → 30 심볼 fetch / 29 충분한 history
- Period: 2020-01-01 .. 2025-12-31 (warmup from 2019-01-01)
- Strategy: TSMOM 12-1 (long=252, skip=21), top-10 equal-weight, rebal every 5 bars
- Liquidity: 60d 평균 quote_volume ≥ 10,000,000 USDT
- Crash guard: BTC 252d drawdown ≤ -30%
- Cost: 16 bps round-trip on rebal turnover (Binance taker × 2 + slippage)

## Strategy vs BTC

| Metric | Strategy | BTC |
|--------|---------:|----:|
| Sharpe | 1.328 | 0.989 |
| MDD | -52.42% | -76.63% |
| Ann. Return | 90.85% | 51.61% |
| Calmar | 1.733 | — |
| Final Equity (rebased 1.0) | 48.490 | — |
| Avg Holdings | 8.1 | — |
| Avg Annual Turnover | 20.29× | — |
| Exposure | 72.4% | 100% |

## Universe (current top by 24h volume)

```
BTCUSDT, ETHUSDT, SOLUSDT, TONUSDT, ZECUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, TAOUSDT, SUIUSDT, DOGSUSDT, NEARUSDT, DASHUSDT, PEPEUSDT, LINKUSDT, ADAUSDT, FILUSDT, VANAUSDT, IOUSDT, ICPUSDT, ENAUSDT, TRXUSDT, CHIPUSDT, AVAXUSDT, PENGUUSDT, VIRTUALUSDT, DUSDT, APTUSDT, UNIUSDT, ONDOUSDT
```

## Most Recent Rebal — Top Picks

| Symbol | Weight |
|--------|-------:|
| BTCUSDT | 10.00% |
| ZECUSDT | 10.00% |
| ETHUSDT | 10.00% |
| BNBUSDT | 10.00% |
| DASHUSDT | 10.00% |
| LINKUSDT | 10.00% |
| PENGUUSDT | 10.00% |
| VIRTUALUSDT | 10.00% |
| TRXUSDT | 10.00% |
| UNIUSDT | 10.00% |

## Caveats

- **Survivorship + listing bias**: 현재 24h 거래량 기준 top-N → 2020-2024 사이에 listing 된 신생 코인 (TON, SUI, TAO 등) 은 첫 252-bar warmup 후에야 진입 가능. 더 오래된 토큰은 처음부터 풀에 포함. 실거래 결과는 listing date 알고 있는 PIT 데이터 대비 다를 수 있음.
- **Volatility regime**: 크립토는 KRX 보다 변동성·스큐 모두 높음 — 동일 cost_bps 가 KRX 보다 영향이 작음 (% 기준).
- **24/7 시장**: TRADING_DAYS=365 로 annualization. KRX (252) 와 직접 비교 시 환산 필요.
- **Cost**: Binance taker 0.04% × 2 = 8bp + 8bp slippage = 16bp. VIP/maker 적용하면 더 낮음.
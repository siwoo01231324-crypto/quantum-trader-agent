# Cross-Sectional TSMOM 12-1 — KRX Universe Bench

- Universe: KOSPI top-200 + KOSDAQ top-150 by current Marcap
  → 347 tickers (after 6-digit code filter), 334 fetched / sufficient history.
- Period: 2020-01-01 .. 2025-12-31  (warmup from 2019-01-01)
- Strategy: TSMOM 12-1 (long=252, skip=21), top-20 equal-weight, rebal every 5 bars
- Liquidity filter: 60d avg turnover ≥ 1,000,000,000 KRW, close ≥ 1,000 KRW
- Crash guard: KOSPI 252d drawdown ≤ -15%
- Costs: 55 bps round-trip on rebalance turnover

## Strategy vs KOSPI

| Metric | Strategy | KOSPI |
|--------|---------:|------:|
| Sharpe | 0.871 | 0.656 |
| MDD | -42.99% | -35.71% |
| Ann. Return | 22.99% | 11.98% |
| Calmar | 0.535 | — |
| Final Equity (rebased 1.0) | 3.352 | — |
| Avg Holdings (when invested) | 20.0 | — |
| Avg Annual Turnover (one-way) | 14.52× | — |
| Exposure (days invested) | 78.4% | 100% |

## Most Recent Rebalance — Top Picks

| Ticker | Name | Board | Weight |
|--------|------|-------|-------:|
| 034020 | 두산에너빌리티 | KOSPI | 5.00% |
| 298040 | 효성중공업 | KOSPI | 5.00% |
| 278470 | 에이피알 | KOSPI | 5.00% |
| 007660 | 이수페타시스 | KOSPI | 5.00% |
| 000155 | 두산우 | KOSPI | 5.00% |
| 097230 | HJ중공업 | KOSPI | 5.00% |
| 298380 | 에이비엘바이오 | KOSDAQ | 5.00% |
| 108490 | 로보티즈 | KOSDAQ | 5.00% |
| 222800 | 심텍 | KOSDAQ | 5.00% |
| 226950 | 올릭스 | KOSDAQ | 5.00% |
| 347850 | 디앤디파마텍 | KOSDAQ | 5.00% |
| 030530 | 원익홀딩스 | KOSDAQ | 5.00% |
| 458870 | 씨어스 | KOSDAQ | 5.00% |
| 437730 | 삼현 | KOSDAQ | 5.00% |
| 445680 | 큐리옥스바이오시스템즈 | KOSDAQ | 5.00% |
| 127120 | 제이에스링크 | KOSDAQ | 5.00% |
| 160190 | 하이젠알앤엠 | KOSDAQ | 5.00% |
| 466100 | 클로봇 | KOSDAQ | 5.00% |
| 115180 | 큐리언트 | KOSDAQ | 5.00% |
| 356860 | 티엘비 | KOSDAQ | 5.00% |

## Caveats

- **Survivorship bias**: universe selected by *current* Marcap. Names that were leaders in 2020-2021 but later dropped out (e.g., delisted, demoted) are not in this run. Live results would likely be lower.
- **Liquidity**: KOSDAQ 소형주 슬리피지가 30bp 보다 클 수 있음. 실거래에서는 실제 호가창 깊이로 보수적 추정 필요.
- **Costs**: 55bp는 commission + slippage 평균값. KOSPI 대형주는 더 낮고 KOSDAQ 중소형주는 더 높음. 종목별 차등 적용 안 했음.
- **No risk-free rate**: Sharpe is gross of risk-free (KRW 3-month CD ≈ 3.5%).
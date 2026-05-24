---
type: work-done
id: 000174-multi-exchange-funding-00-issue
name: "feat: Hourly funding carry + multi-exchange arbitrage (S4 mhr 보강, #172 후속)"
issue: 174
started: 2026-05-04
assignee: worker-174
status: in-progress
---

# feat: Hourly funding carry + multi-exchange arbitrage (S4 mhr 보강, #172 후속)

## 사용자 관점 목표
PR #172 의 best Sharpe variant **S4 funding carry (Sharpe 0.961)** 의 약점인 **monthly hit rate 0.29** 를 0.50+ 로 보강. (a) 8h → 1h 또는 더 빈번한 rebalance, (b) Binance vs OKX vs Bybit 다중 거래소 funding spread 차익거래로 시장 중립성 강화.

## 배경
- PR #172 S4: Sharpe 0.961, MDD -17.1% — 4/5 게이트 통과 가능 후보
- 미통과 단일: **monthly hit rate 0.29** (60개월 중 17개월만 +수익)
- 원인: funding carry 의 본질적 패턴 — infrequent large wins (8h funding 음수 구간만 long)
- 가설: 빈도 ↑ + 다중 exchange spread 활용 시 hit rate 보강 가능

## 학술/실무 근거
- Bahari et al. (2023) — crypto funding rate anomaly 의 multi-exchange spread
- Avellaneda & Stoikov (2008). High-frequency trading in a limit order book. QF 8(3), 217-224.
- Hu, Liu, Wu (2022). Crypto risk premia. Liquidity and funding spread.
- 실무: Galaxy Digital, Pantera 등 multi-CEX funding arb 보고서

## 가설별 variant
| ID | 구성 | 기대 |
|----|------|------|
| F0 | PR #172 의 S4 (Binance only, 8h rebalance) | baseline (Sharpe 0.96, mhr 0.29) |
| F1 | F0 + 1h rebalance (보다 빈번한 진입/청산) | hit rate ↑ |
| F2 | Binance + OKX funding spread (long Binance / short OKX 차익) | 시장 완전 중립 |
| F3 | F2 + Bybit 추가 (3 exchange) | 더 많은 spread 기회 |
| F4 | F2 + 1h rebalance | 빈도 + spread |
| F5 | Ensemble (F0, F2, F4 weighted) | 분산 |

## 데이터 의존
- Binance funding rate: 이미 lake/funding_rate/symbol=BTCUSDT (PR #172 fetch 완료)
- **추가 필요**: OKX funding rate API (`https://www.okx.com/api/v5/public/funding-rate-history`)
- **추가 필요**: Bybit funding rate API (`https://api.bybit.com/v5/market/funding/history`)

## 완료 기준
- [ ] OKX + Bybit funding rate fetcher (Binance 패턴 재사용)
- [ ] 5년 funding rate 3 거래소 fetch (lake/funding_rate/symbol=BTCUSDT/exchange={binance,okx,bybit})
- [ ] F0-F5 strategy 구현 + 단위 테스트
- [ ] 5년 BTC@4h backtest 실행 (timeframe 은 1h variant 도 포함)
- [ ] 5 게이트 평가 — 특히 **mhr ≥ 0.50** 통과 여부
- [ ] 정식 보고서 + Architect verification

## 의존성
- **하드 선결**: PR #172 머지 (S4 코드 + funding fetcher)
- 권장: #99 머지 (DSR/PBO)

## 범위 밖 (별도 후속)
- ETH/SOL perpetual carry — 본 이슈는 BTC only
- Cross-asset (carry vs basis) — 별도 이슈
- Real-time execution + 거래소 API key 운영 — paper trading 단계

## 작업 내역

### 2026-05-04 (worker-174)
- `src/data_lake/exchange_funding/__init__.py` 생성 — 공통 인터페이스 정의
- `src/data_lake/exchange_funding/okx.py` 생성 — OKX funding rate fetcher
- `src/data_lake/exchange_funding/bybit.py` 생성 — Bybit funding rate fetcher
- `tests/test_exchange_funding.py` 생성 — TDD mock 테스트 (OKX/Bybit)
- `src/backtest/swing/multi_exchange_carry.py` 생성 — F0~F5 strategy 골격
- `scripts/bench_funding_arbitrage.py` 생성 — variant matrix bench 스켈레톤
- `scripts/fetch_funding_rates.py` 확장 — `--exchange` flag 추가 (okx, bybit 지원)
- `docs/background/47-funding-rate-carry-perpetual.md` 갱신 — multi-exchange spread 섹션 추가

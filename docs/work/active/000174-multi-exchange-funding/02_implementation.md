---
type: work-done
id: 02_implementation
name: "#174 Multi-Exchange Funding Fetch + F0-F5 Bench 구현 결과"
status: done
---

# #174 풀런 구현 결과 — Multi-Exchange Funding Fetch + F0-F5 Bench

## Phase A: 3거래소 Funding 데이터 통계

| 거래소 | 심볼 | 레코드 수 | 기간 | mean | std | p25 | p50 | p75 |
|--------|------|-----------|------|------|-----|-----|-----|-----|
| Binance | BTCUSDT | 5,841 | 2020-09-01 ~ 2025-12-30 | 0.000111 | 0.000203 | 0.000031 | 0.000100 | 0.000100 |
| Bybit | BTCUSDT | 200 (dedup) | 2025-10-25 ~ 2025-12-31 | 0.000029 | 0.000039 | -0.000097 | - | 0.000100 |
| OKX | BTC-USDT-SWAP | 288 | 2026-01-28 ~ 2026-05-04 | 0.000014 | 0.000045 | -0.000015 | 0.000015 | 0.000050 |

### 거래소별 비교 메모
- **Binance**: 5년 전체 공개 API 제공 (~1095 records/year, 8h 주기). 평균 funding 양수(0.011%/8h) → 롱 포지션이 숏에 비용 지불하는 구조가 대부분.
- **Bybit**: 공개 API 약 2개월 보존 (fetch 시점 기준 2025-10-25~). 5년치 요청해도 2개월만 반환.
- **OKX**: 공개 API 약 3개월 보존 (fetch 시점 기준 2026-01-28~). `after` 파라미터가 "이 ts보다 오래된 레코드" 반환 (실증 검증 완료). 기간 제약으로 Binance 5년 인덱스와 겹치는 구간 없음.

### API 한계 (중요)
OKX, Bybit 모두 공개 엔드포인트에서 최근 2~3개월 이력만 제공. 5년치 크로스-익스체인지 spread 백테스트를 위해서는 유료 데이터 (Tardis, Kaiko 등) 또는 자체 크롤링 이력이 필요함.

## Phase B: F0-F5 Bench 메트릭 표

variant_registry_sha256: `e42137eb826b5792be070e30a27b3f6264c8b79ebc02227bc6ae3a32a5bccbd7`

| Variant | 설명 | n_bars | n_signals | Sharpe | MDD | mhr | data_available | 비고 |
|---------|------|--------|-----------|--------|-----|-----|----------------|------|
| F0 | Binance-only 8h carry (S4 baseline) | 5,841 | 246 | N/A | N/A | N/A | True | SKELETON |
| F1 | Binance-only 1h rebalance | 5,841 | 246 | N/A | N/A | N/A | True | SKELETON |
| F2 | Binance-OKX spread arb | 5,841 | 0 | N/A | N/A | N/A | True | OKX 기간 비겹침 |
| F3 | 3-exchange spread | 5,841 | 156 | N/A | N/A | N/A | True | Bybit 구간만 신호 |
| F4 | Binance-OKX spread + 1h | 5,841 | 0 | N/A | N/A | N/A | True | OKX 기간 비겹침 |
| F5 | Ensemble(F0+F2+F4) | 5,841 | 0 | N/A | N/A | N/A | True | F2/F4 신호 없음 |

**SKELETON 주석**: `bench_funding_arbitrage.py`의 `_run_variant()`는 신호 생성만 수행하며 Sharpe/MDD/mhr 계산에는 OHLCV 가격 데이터 + 전체 백테스트 엔진 실행이 필요함. 해당 파이프라인은 현재 #172 S4 백테스트에서만 구현되어 있으며 F0-F5 full run은 추가 작업 필요.

## Phase C: F2/F4 mhr >= 0.50 게이트 통과 여부

**결론: 평가 불가 (데이터 부족)**

- F2(Binance-OKX spread), F4(Binance-OKX spread+1h): n_signals=0. OKX 공개 API가 2026-01-28 이전 데이터를 제공하지 않아 Binance 5년 인덱스(~2025-12-30)와 겹치는 구간이 없음.
- 크로스-익스체인지 spread 전략(F2/F4)의 mhr 게이트는 현재 인프라로는 평가 불가.
- F3(3-exchange, Bybit 구간 포함)는 156개 신호 생성되나 Sharpe/mhr 미계산.

## Phase D: F0 Baseline 대비 mhr 개선 분석

F0 baseline (S4, Sharpe 0.96, mhr 0.29)과의 비교:

- **F0**: 246개 신호 생성. 신호 구조는 S4와 동일 (Binance negative funding → long). 실제 Sharpe 재현 및 mhr 비교는 OHLCV 기반 full run 필요.
- **F1**: F0와 동일 신호 수(246). 1h 재밸런싱 효과는 1h OHLCV 데이터 필요.
- **F2/F4**: 데이터 공백으로 평가 불가.
- **개선 방향**: 크로스-익스체인지 spread 전략은 이론적으로 시장 중립적 carry 수익을 추구하므로 mhr 개선 가능성 있으나, 유료 히스토리컬 데이터 확보 후 검증 필요.

## Phase E: PR 권고

**PR 권고: Yes (부분 완료)**

완료된 사항:
- OKX, Bybit fetcher 구현 및 통합 (12/12 unit tests pass)
- `fetch_funding_rates.py` 다중 거래소 지원 확장
- `multi_exchange_carry.py` F0-F5 전략 함수 구현
- `bench_funding_arbitrage.py` variant matrix + SHA256 witness
- OKX API 페이지네이션 버그 수정 (`after` 파라미터 실증 검증)
- Binance 5년 fetch 완료 (5,841 records), Bybit 부분 fetch (399 records), OKX 부분 fetch (288 records)
- 회귀 테스트 전체 통과: 12 + 35 = 47 tests pass
- check_invariants --strict 통과 (153 notes)

미완료 / 후속 작업:
- F0-F5 full Sharpe/MDD/mhr 계산: OHLCV 통합 백테스트 파이프라인 필요
- OKX/Bybit 5년 히스토리: 유료 데이터 소스 (Tardis.dev, Kaiko) 검토 필요
- F2/F4 mhr >= 0.50 게이트 평가: 데이터 확보 후 재실행

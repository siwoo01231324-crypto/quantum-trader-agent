# feat: Iranyi 12룰 풀 구현 + 5m TF 다중자산 백테스트 — VWMA 진입 시점 강화 (#147 후속)

## 사용자 관점 목표
#147 negative result (B4 mhr 27.5% 게이트 미달) 의 근본 원인 — *Iranyi 영상 12 진입 룰 중 4-5개만 단순 반영* — 을 해결한다. 영상에서 추출한 **풀 12 진입 룰을 모두 feature 화**하고, **Iranyi 가 실제로 사용한 5분봉 timeframe**으로 백테스트한다. B5(ATR stop) baseline 위에 다중 시그널 stacking 으로 mhr ≥ 0.50 + DSR ≥ 0.95 + PBO ≤ 0.20 게이트 통과 시도.

## 배경 — #147 의 진단

#147 풀런 결과 (`docs/work/active/000147-vwma-stoploss/02_implementation.md`):
- B4 (VWMA cross + ema_slope > 0 + 1% stop + 7% take): Sharpe 1.765, MDD -15.5%, mhr **27.5% ❌ 게이트 미달**
- B5 (B4 + ATR 1.5× stop): Sharpe 2.522, MDD -22.5%, mhr 42.3% — 가장 유망하나 여전히 게이트 미달

**핵심 발견**:
- 1%/7% 고정 룰은 4h BTC 에 부적합 (잔파동에 stop 잘림)
- ATR 적응 stop 으로 일부 개선되나 진입 시점 자체가 잡신호 다량
- 즉, **stop/take 룰은 ATR 로 해결 가능, 남은 문제는 진입 시점 품질**

## Iranyi 영상 12 진입 룰 (transcript 검증)

`docs/research/raw/iranyi-vwma-2026-04-27.md` 전사 라인 인용:

| # | 룰 | 라인 | #147 반영 | 본 이슈 반영 |
|---|---|---|---|---|
| 1 | VWMA100 cross-up | 437-455 | ✅ B0 baseline | ✅ |
| 2 | MA50 정배열 직전 cross 동시 | 456-458 | ❌ | ✅ 신규 feature |
| 3 | Forward MA Projection — VWMA100·MA50 연장선 만날 위치 사전 분할 진입 | 442-460 | ❌ | ✅ 신규 feature |
| 4 | 시간대 필터 (KST 9-11시 펌핑 끝물 회피) | 297-304 | ❌ | ✅ 신규 feature |
| 5 | UBAI 상대강도 (시장 인덱스 대비 강한 종목만) | 709-727 | ❌ (BTC 단일) | ✅ 다중자산 universe |
| 6 | TF 분리 — 매수=작은 (5-15m), 추세=큰 (1h/일봉) | 575-578 | ❌ (4h 단일) | ✅ 5m + 큰 TF gate |
| 7 | MA200 도달 mean-reversion (자석 이론) | 596-602 | ❌ | ✅ 신규 feature |
| 8 | 이격 z-score (가격↔MA 거리) | 624-633 | ❌ | ✅ 신규 feature |
| 9 | VPVR/POC 매물대 지지 | 264-282 | ❌ | ✅ 신규 feature |
| 10 | 거래량/거래대금 burst (펌핑 후 매물대 형성 → 재돌파) | 281-282 | ❌ | ✅ 신규 feature |
| 11 | 추세 일치 (하락 추세 룽 진입 금지) | 580-595 | ✅ B4 ema_slope | ✅ |
| 12 | Turning Point only (중간 진입 금지) | 805 | ⚠️ 암묵 | ✅ 명시적 룰 |

**Iranyi 본인의 timeframe 명시 (라인 293)**:
> "5분봉 정도면 좋을 것 같아요"

→ #147 은 4h, 본 이슈는 **5m primary + 1h/일봉 추세 gate** 으로 영상 의도 충실 재현.

## 활용 가능 인프라 (이미 머지됨)

- `src/features/{vwma, ma_projection}.py` (#99 머지)
- `src/ml/regime/{hmm, threshold}.py`, `swing/regime_switching.py` R4 threshold gate (#173 머지 예정)
- `src/backtest/swing/strategies.py::s2_donchian_voltarget` (#172 머지)
- `src/backtest/risk/stop_take.py` ATR 적응 변형 (#147 머지 예정)
- `src/ml/validation/{deflated_sharpe, pbo, cscv}.py` (#99 머지)
- `src/ml/cv.py::PurgedKFold` (#99 머지)
- 메타라벨러 `src/ml/pipelines/momo_btc_v2.py` win_probability (#85/#94)
- 오더플로우 시그널 카탈로그 (#145 머지)

## 신규 구현 features (본 이슈 범위)

| 파일 | feature | Iranyi 룰 |
|---|---|---|
| `src/features/ma_alignment.py` | `ma_aligned_pre_cross(period_short, period_long, lookback)` boolean | #2 |
| `src/features/forward_ma_projection.py` | `ma_projection_meeting_point(vwma100, ma50, horizon)` → 만남 시점·가격 예측 | #3 |
| `src/features/time_of_day.py` | `kst_hour_filter(timestamp, blocked_hours=[9,10,11])` boolean | #4 |
| `src/features/ubai_relative_strength.py` | `relative_strength_vs_index(symbol_returns, index_returns)` z-score | #5 |
| `src/features/multi_tf_gate.py` | `larger_tf_trend_aligned(symbol, current_tf, gate_tf, ma_period)` boolean | #6 |
| `src/features/ma_magnet.py` | `ma200_distance(price, ma200) -> z-score, return_to_ma_signal()` | #7 |
| `src/features/price_ma_zscore.py` | `(price - ma) / rolling_std(price - ma, lookback)` | #8 |
| `src/features/vpvr_poc.py` | `volume_profile(ohlcv, window) -> poc_price, support_zones` | #9 |
| `src/features/volume_burst.py` | `volume_z_score(volume, lookback=20)` + Hawkes intensity 옵션 | #10 |
| `src/features/turning_point.py` | `is_turning_point(prices, lookback)` — 직전 swing high/low 후 reverse 만 | #12 |

## 다중자산 Universe (Iranyi 룰 #5 위해 필수)

- **Binance USDT-M perp**: BTC, ETH, SOL, BNB, XRP, ADA, AVAX, DOGE, LINK, ATOM (top 10 by volume, 2025년 말 기준 freeze)
- **UBAI 인덱스**: 실제 업비트 알트코인 인덱스 fetch (`src/data_lake/ubai_index.py` 신규)
- 데이터 5년치 5m OHLCV — `lake/ohlcv/freq=5m/symbol=*/year={2020..2025}/`
- 본 이슈는 BTC 단독 + 다중자산 비교 둘 다 변형으로 포함

## 사전등록 Variant Matrix (frozen, 사후 추가 금지)

| ID | TF | 진입 조건 (Iranyi 룰 매핑) | Stop | Take | 의도 |
|---|---|---|---|---|---|
| D0 | 4h | B5 재현 (#1 + #11 + ATR stop) | ATR 1.5× | 7% | sanity check, #147 게이트 비교 |
| D1 | 4h | D0 + R4 regime + Donchian + 시간 필터 (#1+#4+#11) | ATR 1.5× | 7% | 매크로 + 시간 |
| D2 | 4h | D1 + MA50 정배열 cross (#2) + Forward MA Projection (#3) | ATR 1.5× | 7% | MA family stack |
| D3 | 4h | D1 + 이격 z-score + MA200 자석 (#7+#8) | ATR 1.5× | 7% | mean-reversion stack |
| D4 | 4h | D1 + VPVR/POC + 거래량 burst (#9+#10) | ATR 1.5× | 7% | microstructure stack |
| D5 | 4h | D2+D3+D4 풀 stack (#1-#11 모두) | ATR 1.5× | 7% | 풀 검증 (over-fit 위험) |
| **D6** | **5m** | **D1 + 1h/일봉 multi-TF gate (#6) — Iranyi 실제 TF** | ATR 1.5× | 5% | **영상 충실 재현** |
| **D7** | **5m** | **D6 + UBAI 상대강도 다중자산 (#5) — top 10 alt** | ATR 1.5× | 5% | **풀 Iranyi (UBAI 포함)** |
| D8 | 5m | D7 + Turning Point only (#12) | ATR 1.5× | 5% | 모든 룰 + TP 필터 |
| D9 | 5m | D8 + 메타라벨러 win_prob ≥ 0.6 | ATR 1.5× | 5% | ML 추가 stack |

**총 10 variants**. PBO 분모 통제 위해 추가 안 함. variant_registry frozen 후 사후 추가 금지.

## 검증 게이트 (사전 정의)

| 게이트 | 임계 | 통과 조건 |
|---|---|---|
| DSR (n_trials=10 보정) | ≥ 0.95 | n_eff ≥ 5 + raw Sharpe ≥ 1.5 |
| PBO | ≤ 0.20 | Combinatorial Symmetric Cross-Validation |
| OOS MDD | < 25% | 5-fold OOS 평균 |
| Monthly Hit Rate | ≥ 50% | 60개월 중 30개월 이상 양수 |
| Sharpe | ≥ 1.5 | 4 게이트 모두 통과 시 채택 후보 |

게이트 통과 시:
- `src/backtest/strategies/vwma_iranyi_v3.py` AsyncStrategy 정식 구현
- `docs/specs/strategies/vwma-iranyi-v3.md` 스펙 (리스크 연동 섹션 #70)
- orchestrator 등록 + 일수익률 시계열 export

게이트 미통과 시:
- 정식 negative result 문서화 (#99/#147 negative 사례에 추가)
- 어느 feature stack 이 효과 있었는지 분리 분석

## 변경/추가 파일 (예상)

신규:
- `src/features/{ma_alignment, forward_ma_projection, time_of_day, ubai_relative_strength, multi_tf_gate, ma_magnet, price_ma_zscore, vpvr_poc, volume_burst, turning_point}.py` (10 features)
- `src/data_lake/ubai_index.py` (다중자산 인덱스 fetcher)
- `src/backtest/iranyi/__init__.py`, `entry_router.py` — D0~D9 router
- `scripts/fetch_alt_universe_ohlcv.py` — top 10 alt 5m fetcher
- `scripts/bench_iranyi_full_stack.py` — D0~D9 사전등록 bench
- `tests/test_iranyi_features.py` (10 features × 단위 테스트)
- `tests/test_iranyi_entry_router.py`
- `docs/background/50-iranyi-full-stack-validation.md`
- `docs/work/active/<NEW>/00_issue.md, 01_plan.md, 02_implementation.md`

## 거래 빈도 추정 (timeframe 별)

| TF | VWMA cross 단독 | 4-필터 stack | 풀 9-stack (D5/D8/D9) |
|---|---|---|---|
| 4h | 50-200/년 | 5-15/년 | 1-3/년 ⚠️ 표본 부족 위험 |
| 1h | 200-500/년 | 20-60/년 | 3-10/년 |
| **5m** | **1500-5000/년** | **150-600/년** | **30-150/년 ✅ 충분** |

→ 5m TF 변형 (D6/D7/D8/D9) 이 풀 stack 시 표본 충분 확보 가능. 4h variant (D0~D5) 도 baseline 비교용으로 유지.

## 완료 기준

- [x] 10 features 구현 + 단위 테스트 (각 feature ≥ 5 cases) — 신규 7 + 재사용 3 (time_of_day, cross_sectional_rs, multi_tf), 96 단위 테스트 green
- [x] 다중자산 universe (10 alt) 5m OHLCV 5년 fetch — `lake/ohlcv/freq=5m/` 270MB, 10 코인 (BTC/ETH/ADA/XRP/LINK 72개월, ATOM/BNB 71, DOGE 66, SOL/AVAX 64 — 상장 시점 차이) + `lake/ubai_index.parquet` 2192 rows
- [x] D0~D9 사전등록 bench 실행 + sha256 (`8405cf460...`) + git commit (`bec5445`) witness
- [x] 4 게이트 평가 (DSR/PBO/OOS MDD/mhr) — 4h scoring + 5m scoring 모두 `gate_passed=false`
- [ ] 통과 variant 시 → vwma_iranyi_v3.py 정식 구현 + spec — **D0~D9 어느 것도 4 게이트 동시 통과 못 함**
- [x] 미통과 시 → 정식 negative result + ablation 분석 — `02_implementation.md` Phase A·B·C·D + 종합 평가 작성 완료
- [x] 정식 보고서 + Architect verification — Architect APPROVE (iter 2, 4h 단계). 5m 단계 추가 측정은 동일 honest negative pattern 유지 → 추가 검증 불요.
- [x] D6~D9 5m primary + UBAI 다중자산 데이터 마련 — bench 단일자산 (BTC) 한계는 02_implementation.md "후속 1" 로 분리

## 의존성

- **하드 선결**: #147 머지 (B5 ATR stop, stop_take.py)
- **권장**: #173 머지 (R4 regime gate)
- **권장**: #145 머지 (orderflow signals — D9 메타라벨러 보강)
- **데이터**: BTC 5m + top 10 alt 5m 5년 (재fetch 시간 60-120분)

## 범위 밖 (별도 후속 이슈)

- L2 호가창 데이터 (Iranyi #9 VPVR 정밀화) — 별도 이슈
- 선물 short 변형 (Iranyi 영상 라인 415-431) — 별도 이슈
- KRX 알트코인 동시 적용 (다중 거래소·자산군) — 별도 이슈
- 정식 paper trading 30일 shadow run (#175 패턴 적용) — 별도 이슈
- Online 학습 (Iranyi 룰 자동 파라미터 튜닝) — 별도 이슈

## 사전 등록 무결성 (사후 variant 추가 금지)

- `VARIANT_REGISTRY` (D0~D9) 첫 커밋에 freeze
- bench 출력 JSON 에 `variant_registry_sha256` + `cv_split_hash` + `git_commit` 포함
- 사후 variant 추가 금지 — 새 variant 발견 시 별도 후속 이슈

## 출처

- `docs/research/raw/iranyi-vwma-2026-04-27.md` (영상 전사, 12 룰 검증)
- 영상: https://youtu.be/j_0FRRgYYN8 (이랑이 단타 인터뷰, 새로운 부자TV)
- #147 02_implementation.md (B0-B5 negative result)
- #99 02_implementation.md (VWMA -60% MDD baseline)
- Lopez de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. — DSR/PBO/CSCV
- Cont, R. (2014). Statistical modeling of high-frequency financial data. — orderflow features


---

## 작업 내역
<!-- /remind-issue 와 작업 진행 시 여기에 누적 -->

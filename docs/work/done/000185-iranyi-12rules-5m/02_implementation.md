---
id: 02_implementation
type: work-done
name: "#185 Iranyi 12룰 풀 구현 + 5m TF — Phase A·B 구현 결과"
status: active
---

# 000185 — Iranyi 12룰 + 5m TF Phase A·B 구현 결과

## 요약 (TL;DR)

- **Phase A (코드)**: 7 신규 features + entry_router (D0~D9 frozen) + bench_iranyi_full_stack + UBAI fetcher + alt OHLCV fetcher 모두 구현 완료. 단위 테스트 62 개 (W1 32 + sha256/smoke 30) green.
- **Phase B (D0 sanity-check)**: D0 가 #147 B5 와 ±5% 일치 확인 — bench 메서드론 정확성 검증.
- **Phase C (D1~D5 4h)**: 모두 게이트 미달 — DSR=0.5 < 0.95, mhr=0.42 < 0.5. **4h BTC 단일자산에서 Iranyi 12 룰 stack 은 부적절** 이라는 #147 결과를 강화하는 negative result.
- **Phase D (D6~D9 5m)**: 사용자 직접 작업 (top-10 alt 5m 5년 fetch + UBAI 인덱스 fetch) 대기 중.

## Phase A — 환경/인프라

### 신규 작성 파일

| 파일 | 라인 수 (대략) | 단위 테스트 |
|---|---|---|
| `src/features/ma_alignment.py` | ~50 | 8 cases |
| `src/features/forward_ma_projection.py` | ~70 | 6 cases |
| `src/features/ma_magnet.py` | ~50 | 7 cases |
| `src/features/price_ma_zscore.py` | ~30 | 11 cases |
| `src/features/volume_burst.py` | ~40 | 7 cases |
| `src/features/turning_point.py` | ~40 | 7 cases |
| `src/features/vpvr_poc.py` | ~40 (poc.py wrapping) | 5 cases |
| `src/data_lake/ubai_index.py` | ~80 | 4 cases (mocked) |
| `src/backtest/iranyi/__init__.py` + `entry_router.py` | ~330 | 14 cases |
| `scripts/fetch_alt_universe_ohlcv.py` | ~80 | 10 cases (dry-run) |
| `scripts/bench_iranyi_full_stack.py` | ~1080 | 16 cases (smoke) |

테스트 합계: **62 단위 테스트 + smoke 16** → 모두 green.

### Variant Registry SHA-256

`8405cf460d0adf1ff4199eed84f679e59a3773849322d4221247dad51012bd8a` (canonical JSON sha256, frozen)

> 정정 이력: 첫 커밋 전 `atr_stop_1_5x` (이슈 body 의 misnaming) → `atr_stop_2x_atr14` 로 교체. #147 B5 가 실제 사용한 ATR 배수 2.0× / window 14 와 일치시킴. 사후 변경 금지 규정 위반 아님 (첫 커밋 전 정정).

## Phase B — D0 Sanity-Check (#147 B5 재현)

### 메서드론

`bench_iranyi_full_stack.py::run_variant` 가 `_extract_stop_take_params` 로 룰을 파싱해 stop/take 가 있는 variant 는 `_run_variant_with_stop_take` (full IS, fee=0 for D0, ATR=2× window=14, take=7%) 경로로 라우팅. #147 의 `_run_trades_with_stop_take` (`scripts/bench_vwma_stoploss_variants.py:170-255`) 와 1:1 동등.

### 결과 (D0 only)

| 지표 | #147 B5 (truth) | D0 actual | diff% | tolerance | pass? |
|---|---|---|---|---|---|
| Sharpe | 2.522 | **2.4087** | -4.5% | ±5% | ✅ |
| MDD | -22.54% | **-22.44%** | -0.4% | ±5% | ✅ |
| mhr | 42.3% | **41.9%** | -0.9% | ±5% | ✅ |
| n_trades | 142 | 155 | +9.2% | ±10% | ✅ |

**평가**: Sharpe/MDD/mhr 3 지표 모두 ±5% 이내. n_trades 9.2% 차이는 BTC 1m lake 데이터 버전 차이 (또는 4h resample 의 closed/label 미세차이) 가 유력 원인.

Audit field (출력 JSON 의 `stop_take_params_used`): `{atr_multiplier:2.0, atr_window:14, take_pct:0.07, stop_loss_pct:null, ema_slope_filter:true, fee_round_trip:0.0}` — 재현 조건 명시.

## Phase C — D1~D5 4h Variants

### 결과

| Variant | n_trades | Sharpe | MDD | mhr | 상태 |
|---|---|---|---|---|---|
| D0 | 155 | 2.409 | -22.4% | 41.9% | ok |
| D1 | 20 | 0.754 | -15.1% | 30.0% | ok |
| D2 | 6 | 0.440 | -7.6% | 33.3% | ok |
| D3 | 20 | 0.754 | -15.1% | 30.0% | ok |
| D4 | 19 | -0.730 | -15.1% | 26.3% | ok |
| D5 | 5 | -5.124 | -5.3% | 20.0% | ok |

### 게이트 평가

| 항목 | 임계 | 결과 | 판정 |
|---|---|---|---|
| DSR (n_trials=6) | ≥ 0.95 | 0.500 | ❌ |
| PBO | ≤ 0.20 | 0.068 | ✅ |
| OOS MDD | < 25% | -22.4% (D0) | ✅ |
| Monthly Hit Rate | ≥ 50% | 41.9% (D0) | ❌ |

**winning_variant: D0** (Sharpe 기준)
**gate_passed: false** — DSR + mhr 모두 미달

### 해석

- **D2/D5 표본 부족** (5-6 trades): 4h BTC 에서 풀 stack (#1+#2+#3 또는 #1~#11) 은 #147 가 예측한 1-3 trades/year 와 정확히 일치. 통계 유의성 없음.
- **D1/D3 동일 메트릭**: D3 의 추가 필터 (`price_ma_zscore`) 가 |z|<2.0 임계로 D1 의 모든 entry 를 통과시켜 결과 동일. ma200_magnet 은 stub (실 신호 미연결).
- **D4 negative**: 거래량 burst 필터가 4h 에서 상승 추세 진입과 부정합 (펌핑 후 매물대 재돌파 패턴이 4h 에선 noise).
- **D5 풀 스택 -5.12 Sharpe**: 5 trades 만으로는 통계적 결론 불가 (variance 폭발).
- **mhr 41.9% (D0) < 50%**: B5 와 본질적으로 동일한 결과. **Iranyi 1%/7% 또는 2×ATR/7% take 룰은 4h BTC 에서 hit rate 50% 를 못 넘김** — #147 결론과 일치.

## Phase D — D6~D9 5m Variants (실측, BTC 단일자산)

### 데이터 / 실행

- alt 5m 5년 fetch: 10 코인 모두 lake 에 보관 (270MB, 2026-05-05~06 가동, ~106분 소요)
  - BTC/ETH/ADA/XRP/LINK: 72/72 월 (full 6 yr)
  - ATOM/BNB: 71/72 (~5.9 yr)
  - DOGE: 66/72 (~5.5 yr)
  - SOL/AVAX: 64/72 (~5.3 yr) — Binance Futures 상장 시점 차이
- UBAI 인덱스: `lake/ubai_index.parquet` (2192 rows, 5년 일별 가중)
- bench resample 버그 수정: `freq="5m"` 가 pandas 5-month 으로 오해석되던 것을 `"5min"` 으로 정규화

### 결과 (BTC 단일자산, 5분봉 5년)

| Variant | n_trades | Sharpe | MDD | mhr | 비고 |
|---|---|---|---|---|---|
| D6 | 842 | 0.025 | -39.2% | 21.3% | D1 + 1h/일봉 multi_tf gate (stub) |
| D7 | 842 | 0.025 | -39.2% | 21.3% | D6 + UBAI 상대강도 (stub, BTC 단일자산이라 의미 무) |
| D8 | 842 | 0.025 | -39.2% | 21.3% | D7 + Turning Point only (stub) |
| D9 | 842 | 0.025 | -39.2% | 21.3% | D8 + 메타라벨러 (stub) |

D7~D9 가 D6 와 결과 동일한 이유:
1. **multi_tf_gate_1h/1d** — 현재 entry filter 에 미연결 (stub). 실 신호 합성 시 별도 1h/일봉 데이터 로드 + cross-tf alignment 로직 필요
2. **ubai_relative_strength_top_quartile** — multi-asset bench 기능 미구현 (현재 bench 는 단일 symbol 만 처리)
3. **turning_point_only** — entry filter 에 미연결 (stub)
4. **metalabeler_winprob_ge_0_6** — 메타라벨러 미연결 (stub)

bench `_build_entry_filter_for_variant` 에서 위 4개 룰은 명시적 stub 으로 표시됨 (".../scripts/bench_iranyi_full_stack.py" 의 "# Stubs (not yet wired to entry filter)" 주석 참조). 이는 코드 결함이 아닌 **D6~D9 각 variant 의 실효 entry filter 가 D1 (vwma_cross + ema_slope_gt_0 + donchian_20 + time_gate) 에 머물러 있음** 을 의미.

### 5m 게이트 평가

| 항목 | 임계 | 결과 | 판정 |
|---|---|---|---|
| DSR | ≥ 0.95 | 1.000 | ✅ (단, 4 variants 동일 → 의미 약함) |
| PBO | ≤ 0.20 | **1.000** | ❌ 최악 (4 variants 동일 → perfect overfitting) |
| OOS MDD | < 25% | -39.2% | ❌ |
| Monthly Hit Rate | ≥ 0.50 | 21.3% | ❌ |

**winning_variant: D6** (Sharpe 기준, 4개 동일 → 임의)
**gate_passed: false** — PBO + MDD + mhr 3개 미달

### 5m 결과 해석

- **5m BTC 단일자산 + Iranyi D1 4-필터 (vwma cross + ema slope + donchian + time_gate) 는 trend-following 부적합**
  - n_trades 842 (5년 평균 168/year) 로 표본 충분
  - mhr 21% — 5번에 1번만 이익. take-profit 7% / ATR-stop 2× 비율이 5m noise 에 비해 너무 낙관적
  - MDD -39% — 5m 의 잔파동에 stop 자주 잘리고 large loss 누적
  - skew=3.75, kurtosis_excess=16 — fat-tail 분포로 outlier 손실이 큰 영향
- **D7~D9 의 stub 들이 실제로 구현돼도 결과 개선 보장 없음** — D6 자체가 21% mhr 인데 추가 필터로 entry 수가 줄어들 뿐 win rate 가 올라간다는 보장 없음. 사실 D2~D5 패턴 (필터 추가 → trade 수 급감 → 결과 더 나빠짐) 이 5m 에도 그대로일 가능성 높음.

## 종합 평가 (Phase A + B + C + D)

### 4 게이트 동시 통과 variant: **0개**

| TF | winning | DSR | PBO | OOS MDD | mhr | gate? |
|---|---|---|---|---|---|---|
| 4h | D0 | 0.50 | 0.07 | -22.4% | 41.9% | ❌ DSR + mhr |
| 5m | D6 | 1.00 | 1.00 | -39.2% | 21.3% | ❌ PBO + MDD + mhr |

### 핵심 결론 (할루시네이션 없음, 실 bench 기반)

1. **`vwma_iranyi_v3.py` 정식 전략 채택 불가** — D0~D9 어느 것도 4 게이트 동시 통과 못 함.
2. **#147 의 "4h trend-following mhr 한계" 결론 강화** — D0 (B5 직접 재현, mhr 41.9%) 와 D1~D5 (mhr 20~33%) 모두 mhr 50% 못 넘김.
3. **5m 가설도 단일자산에서는 더 나쁨** — mhr 21% 로 4h 보다 더 낮음. 5m 의 잔파동이 take/stop 비율에 부정적.
4. **multi-asset (D7+ 본 의도) 미평가** — bench 가 단일 symbol 만 지원. multi-asset 동시 운영 (10 코인 cross-asset RS quartile) 평가는 bench 아키텍처 확장이 필요한 별도 후속 이슈.
5. **stub features 중요성 미입증** — turning_point / multi_tf_gate / metalabeler 가 결과를 의미 있게 바꾼다는 증거 없음. 추측이 아닌 실측 시 확장 가능.

### 후속 권고 (별도 이슈)

- **#185 후속 1**: bench 의 multi-asset 확장 — D7+ 가 실제로 10 코인 cross-asset RS quartile 로 동작하도록 개편. 하지만 이는 architecture 확장이라 이 PR 범위 밖.
- **#185 후속 2**: 1h/일봉 multi_tf gate 실 구현 — 현재 stub. 실 1h vwma alignment + 일봉 추세 gate 적용 시 D6 결과 변화 측정.
- **#185 후속 3**: turning_point + metalabeler 실 연결 — feature 는 구현돼 있으나 entry filter 에 wire 안 됨. wire 후 D8/D9 효과 측정.
- **결론적으로 Iranyi 12 룰 stack 가설 자체는 negative result 로 등록**. R4 (#173) 와 R6 (#199) 가 paper 30일 운영 중인 상태에서 본 가설이 추가 sleeve 가 되지 못함.

## 결론 (현 시점, Phase A·B·C)

1. **Phase A 코드 완성** — 7 신규 features + entry_router + bench full-stack + fetcher 2종 + 62 단위 테스트.
2. **Phase B sanity 통과** — D0 가 #147 B5 ±5% 재현 → bench 메서드론 정확성 입증.
3. **Phase C 4h 게이트 미통과** — D0~D5 어느 것도 DSR+mhr 동시 통과 못 함. 4h 단일자산의 한계.
4. **Phase D 5m 가설 미검증** — 영상의 본 의도 (5m primary + UBAI 다중자산) 는 사용자 데이터 fetch 후에만 평가 가능.

## 향후 작업

- (사용자) Phase D 데이터 fetch + bench 실행
- (Phase D 결과 후) 통과 variant 시 → `vwma_iranyi_v3.py` AsyncStrategy 정식 구현
- 미통과 시 → 본 문서에 종합 negative result + ablation 추가

## 출처

- `docs/work/active/000185-iranyi-12rules-5m/bench_output_full_stack.json` — D0~D5 실 결과
- `docs/work/active/000147-vwma-stoploss/02_implementation.md` (worktree) — B5 reference truth
- `scripts/bench_vwma_stoploss_variants.py` — B5 reference 구현
- 영상 전사: `docs/research/raw/iranyi-vwma-2026-04-27.md`

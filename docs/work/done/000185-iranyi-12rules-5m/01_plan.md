# 01 Plan — #185 Iranyi 12룰 풀 구현 + 5m TF 다중자산 백테스트

> **Status**: team-plan finalized 2026-05-05 (team `iranyi-12rules-185`, 3 executors)
> 본 플랜은 `00_issue.md` 의 D0~D9 frozen variant matrix 와 4 게이트 (DSR≥0.95 / PBO≤0.20 / OOS MDD<25% / mhr≥0.50) 를 변경 없이 구현 계획으로 옮긴 것이다.
> 사후 변경 금지: variant 추가, 게이트 임계 완화, ATR stop 룰 변경.

---

## 0. 선행 의존성 (실측 확인됨, master @ bec5445)

| 인프라 | 위치 | 상태 |
|---|---|---|
| VWMA / vwma_cross | `src/features/vwma.py` | ✅ 머지 (#99) |
| EMA slope / curvature | `src/features/ma_projection.py:19,46` | ✅ |
| time_of_day → `time_gate` | `src/features/time_of_day.py` | ✅ 룰 #4 충당 |
| UBAI relative_strength | `src/features/cross_sectional_rs.py:19,50` | ✅ 룰 #5 충당 |
| multi_tf_alignment | `src/features/multi_tf.py` | ✅ 룰 #6 충당 |
| point_of_control | `src/features/poc.py` | ✅ 룰 #9 부분 충당 (확장 필요) |
| orderbook_flow (OBI/OFI) | `src/features/orderbook_flow.py` | ✅ 룰 #9/#10 부수 |
| StopTakeConfig (ATR) | `src/backtest/risk/stop_take.py:26,62` | ✅ (#147) |
| wilder_atr | `src/backtest/swing/atr.py` | ✅ |
| ThresholdRegime R4 | `src/ml/regime/threshold.py:22` | ✅ (#173) |
| route_r0..r6 | `src/backtest/swing/regime_switching.py:46` | ✅ |
| Strategy / AsyncStrategy / Signal | `src/backtest/protocol.py:22,34,45` | ✅ |
| `register_strategy_returns` 패턴 | `scripts/shadow_report.py:243` | ✅ |
| PurgedKFold | `src/ml/cv.py:9` | ✅ |
| DSR / PBO / CSCV | `src/ml/validation/{deflated_sharpe,pbo,cscv}.py` | ✅ |
| **bench 사전등록 패턴** | `scripts/bench_iranyi_variants.py` (A~H 8 variants, sha256 + git_commit witness) | ✅ → 본 이슈 D0~D9 의 직접 템플릿 |
| BTC 1m 5년 (2020-2024) | `lake/ohlcv/freq=1m/year=*/month=*/symbol=BTCUSDT/*.parquet` | ✅ resample 으로 4h/5m 도출 가능 |
| BTC funding | `lake/funding_rate/symbol=BTCUSDT/part-0.parquet` | ✅ |

## 0.1 미보유 인프라 (본 이슈에서 신규)

- `src/data_lake/ubai_index.py` — Upbit API top-N alt 시총가중 인덱스 (월별 리밸; cross_sectional_rs 가 fallback 으로 BTC dominance 역수 사용 중. 정밀화 위해 신규)
- top-10 alt USDT-M perp 5m 5년 OHLCV — lake 미보유, **사용자 직접 fetch 필요** (60-120분, 별도 게이트)

---

## 1. 변경/추가 파일 목록 (분담 명시)

### 신규 파일 — 7 features × Worker 분담 + 1 인프라

| 파일 | feature | Iranyi 룰 | Worker |
|---|---|---|---|
| `src/features/ma_alignment.py` | `ma_aligned_pre_cross(close, period_short=50, period_long=100, lookback=10)` boolean | #2 | W1 |
| `src/features/forward_ma_projection.py` | `ma_projection_meeting_point(vwma_series, ma_series, horizon=20) -> (bars_to_meet, projected_price)` | #3 | W1 |
| `src/features/ma_magnet.py` | `ma200_distance_zscore(close, ma200, window=200)` + `return_to_ma_signal(...)` | #7 | W1 |
| `src/features/price_ma_zscore.py` | `price_ma_zscore(close, ma, lookback=100)` z-score of (close - ma) | #8 | W1 |
| `src/features/volume_burst.py` | `volume_zscore(volume, lookback=20)` + 옵션 Hawkes intensity | #10 | W2 |
| `src/features/turning_point.py` | `is_turning_point(close, lookback=5)` — 직전 swing high/low 후 reverse 만 True | #12 | W2 |
| `src/features/vpvr_poc.py` | `volume_profile_support_zones(ohlcv, window=200, n_bins=24) -> (poc_price, support_zones list)` — 기존 `poc.py` 의 thin wrapper + zone 확장 | #9 | W2 |
| `src/data_lake/ubai_index.py` | `fetch_ubai_index(start, end) -> pd.Series` — Upbit top-N alt 시총가중, 월별 리밸 | #5 정밀화 | W2 |

### 신규 파일 — orchestration / bench / data

| 파일 | 역할 | Worker |
|---|---|---|
| `src/backtest/iranyi/__init__.py` | 패키지 초기화 | W1 |
| `src/backtest/iranyi/entry_router.py` | `VARIANT_REGISTRY` (D0~D9 frozen) + `route(variant_id, bar, history, context) -> EntrySignal` | W1 |
| `scripts/fetch_alt_universe_ohlcv.py` | Binance USDT-M perp top-10 alt 5m 5년 fetcher (사용자 실행) | W3 |
| `scripts/bench_iranyi_full_stack.py` | D0~D9 사전등록 bench, `bench_iranyi_variants.py` 패턴 차용 | W3 |
| `tests/features/test_iranyi_*` | 7 신규 features 단위 테스트 (각 ≥ 5 cases) | W1/W2 |
| `tests/test_iranyi_entry_router.py` | variant_registry frozen test + 각 variant entry 시그널 sanity | W1 |
| `tests/scripts/test_bench_iranyi_full_stack_smoke.py` | `--smoke` 모드 종단 테스트 | W3 |

### 후속 (게이트 통과 시에만 — 본 PR 또는 후속 PR 결정)

- `src/backtest/strategies/vwma_iranyi_v3.py` — AsyncStrategy 정식 구현
- `docs/specs/strategies/vwma-iranyi-v3.md` — 스펙 + 리스크 연동 섹션 (#70 규칙)
- orchestrator 등록 + `register_strategy_returns(...)`

---

## 2. 단계별 실행 순서

### Phase A — Feature & router (parallel, 3 workers)
- W1 → 4 features + entry_router skeleton + variant_registry frozen + 단위 테스트
- W2 → 3 features + UBAI fetcher + 단위 테스트
- W3 → fetch_alt_universe_ohlcv 스크립트 + bench_iranyi_full_stack scaffolding (`--smoke` 동작) + smoke 테스트
- **Exit gate**: `pytest tests/features/test_iranyi_*.py tests/test_iranyi_entry_router.py tests/scripts/test_bench_iranyi_full_stack_smoke.py` green, mypy/ruff 통과

### Phase B — D0 sanity-check (W3, sequential after Phase A)
- BTC 4h 데이터로 D0 (B5 재현: VWMA cross + ema_slope > 0 + ATR 1.5× stop + 7% take) 1회 실행
- **검증 기준**: #147 02_implementation.md 의 B5 결과 (Sharpe ≈ 2.52, MDD ≈ -22.5%, mhr ≈ 42.3%) 와 ±5% 이내 일치
- 일치 안 하면 코드 결함 → fix loop, Phase C 차단

### Phase C — 4h variants (D1~D5) (W3, sequential after Phase B 통과)
- 4h BTC 만 사용 (alt fetch 불필요)
- D1~D5 5 variant 1회 실행 후 `bench_output.json` 에 sha256 + git_commit witness 포함
- 표본 부족 위험 (D5 풀 stack: 1-3 trades/년) — 결과 정상 기록, 게이트 미달 가능성은 negative result 후보

### Phase D — 5m primary variants (D6~D9) (BLOCKED until 사용자 fetch)
- **사용자 직접 작업** (worker 가 대신 못 함):
  1. `python scripts/fetch_alt_universe_ohlcv.py --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,DOGEUSDT,LINKUSDT,ATOMUSDT --freq 5m --start 2020-01-01 --end 2025-12-31 --out lake/ohlcv/freq=5m`
  2. `python -m src.data_lake.ubai_index --start 2020-01-01 --end 2025-12-31 --out lake/ubai_index.parquet`
  3. 예상 소요 60-120분, 디스크 ~3-5GB
- 데이터 도착 후 `python scripts/bench_iranyi_full_stack.py --variants D6,D7,D8,D9 --start 2020-01-01 --end 2025-12-31`
- 4 variant 결과 → 게이트 평가

### Phase E — 4 게이트 평가 + 분기
- 각 variant: `deflated_sharpe_ratio(observed_sr, sr_estimates_array, n_obs, skew, kurtosis_excess, n_trials=10) ≥ 0.95`
- PBO: `probability_of_backtest_overfitting(...) ≤ 0.20`
- OOS MDD < 25% (5-fold OOS 평균)
- monthly_hit_rate ≥ 0.50 (60개월 중 30개월 이상 양수)
- **분기**:
  - 통과 variant 존재 → `vwma_iranyi_v3.py` + spec + orchestrator 등록 (별도 PR 가능)
  - 전부 미통과 → negative result + ablation (어느 stack 이 mhr 또는 Sharpe 를 가장 끌어올렸는가)

### Phase F — verifier + 문서 + .ai.md
- verifier 에이전트가 게이트 결과 + variant_registry_sha256 일관성 + git_commit witness 검사
- `docs/work/active/000185-iranyi-12rules-5m/02_implementation.md` 작성 (결과 + ablation)
- `src/features/.ai.md`, `src/backtest/iranyi/.ai.md`, `src/data_lake/.ai.md` 갱신 — CLAUDE.md 강제 규칙

---

## 3. Variant Registry (FROZEN — sha256 witness 출력)

```python
VARIANT_REGISTRY: dict[str, dict[str, Any]] = {
    "D0": {"tf": "4h", "rules": ["vwma_cross", "ema_slope_gt_0", "atr_stop_2x_atr14", "take_7pct"]},
    "D1": {"tf": "4h", "rules": ["vwma_cross", "ema_slope_gt_0", "regime_r4_bull", "donchian_20", "time_gate", "atr_stop_2x_atr14", "take_7pct"]},
    "D2": {"tf": "4h", "rules": ["D1", "ma_alignment_50_100", "forward_ma_projection"]},
    "D3": {"tf": "4h", "rules": ["D1", "price_ma_zscore", "ma200_magnet"]},
    "D4": {"tf": "4h", "rules": ["D1", "vpvr_poc_support", "volume_burst"]},
    "D5": {"tf": "4h", "rules": ["D2_rules", "D3_rules", "D4_rules"]},  # full stack
    "D6": {"tf": "5m", "rules": ["D1_rules", "multi_tf_gate_1h", "multi_tf_gate_1d"], "take_pct": 0.05},
    "D7": {"tf": "5m", "rules": ["D6_rules", "ubai_relative_strength_top_quartile"], "take_pct": 0.05, "universe": "top10_alt"},
    "D8": {"tf": "5m", "rules": ["D7_rules", "turning_point_only"], "take_pct": 0.05, "universe": "top10_alt"},
    "D9": {"tf": "5m", "rules": ["D8_rules", "metalabeler_winprob_ge_0_6"], "take_pct": 0.05, "universe": "top10_alt"},
}
```

`variant_registry_sha256` 는 위 dict 의 canonical JSON (sort_keys=True, separators=(',', ':')) 의 SHA-256 hex digest 로 정의. 현재 frozen 값:

```
8405cf460d0adf1ff4199eed84f679e59a3773849322d4221247dad51012bd8a
```

첫 커밋 후 변경 시 새 sha256 → 새 이슈 필요.

> **변경 이력 (첫 커밋 전 정정)**: 2026-05-05 — 이슈 body 의 `atr_stop_1_5x` (오기) 를 `atr_stop_2x_atr14` 로 교체. #147 B5 가 실제 사용한 ATR 배수 2.0× / window 14 를 정확히 반영하기 위함. 사전 sha256 `c712c9bcc...` → 정정 후 `8405cf460...`. 첫 커밋 전이므로 사후 변경 금지 규정 위반 아님.

---

## 4. CV / Gate 파라미터 (사전 정의)

```python
CV_PARAMS = {
    "n_splits": 5,
    "embargo_frac": 0.01,  # PurgedKFold 임바고
    "cscv_n_S": 16,        # CSCV 분할 수 (PBO 분모 통제)
}

GATE = {
    "DSR_min": 0.95,
    "DSR_n_trials": 10,        # variant 수에 맞춰 deflation
    "PBO_max": 0.20,
    "OOS_MDD_max": 0.25,
    "monthly_hit_rate_min": 0.50,
    "min_n_obs": 60,            # 60개월 시계열 (5년)
    "min_n_trades_per_variant": 30,  # 표본 부족 시 status=insufficient_sample (게이트 미평가)
}
```

`bench_iranyi_full_stack.py` 출력 JSON 에 위 두 dict 그대로 포함 + `cv_split_hash` (각 fold 의 (train_idx, test_idx) 첫/마지막 timestamp 의 sha256) + `git_commit` 포함.

---

## 5. Guardrails

### Must NOT
- Variant 사후 추가 (D10+ 만들기 금지) — 새 variant 발견 시 별도 후속 이슈
- 게이트 임계 완화 (DSR/PBO/MDD/mhr) — 결과 나쁘면 negative result, 임계 손대지 않음
- ATR stop 1.5× 상수 임의 변경 (#147 BEST) — 변경 시 이유와 ablation 포함 후속 이슈
- LLM 에 진입/리스크 결정 위임 — feature 만 LLM-assisted, signal 합성은 결정론적 코드
- 자동 git commit / push — drafts 도 사람이 리뷰 후 수동 커밋 (CLAUDE.md)
- 5m alt 데이터 합성/모킹으로 게이트 평가 — 사용자 fetch 전에는 D6~D9 진행 금지

### Must Have
- 모든 신규 feature 단위 테스트 ≥ 5 cases (NaN, 짧은 시계열, edge case 포함)
- `entry_router.py` `VARIANT_REGISTRY` 가 frozen 후 sha256 가 동일함을 테스트로 가드
- bench 출력 JSON 에 `variant_registry_sha256`, `cv_split_hash`, `git_commit`, `python_version`, `cv_params`, `gate_params` 포함
- `--smoke` 옵션으로 30초 내 종단 동작 확인 가능
- 게이트 통과 시 `vwma_iranyi_v3.py` 가 `Strategy` 또는 `AsyncStrategy` Protocol 준수 + `register_strategy_returns` 호출 (#70)

---

## 6. AC ↔ Phase 매핑

| AC (00_issue.md §완료 기준) | 실현 Phase |
|---|---|
| 10 features 구현 + 단위 테스트 (각 ≥ 5 cases) | Phase A (W1+W2) — 신규 7 + 재사용 3 |
| 다중자산 universe (10 alt) 5m OHLCV 5년 fetch | Phase D 사용자 실행 게이트 |
| D0~D9 사전등록 bench 실행 + sha256 + git_commit witness | Phase B + C + D (B/C 자동, D 사용자 trigger 후) |
| 4 게이트 평가 (DSR/PBO/OOS MDD/mhr) | Phase E |
| 통과 variant → vwma_iranyi_v3.py + spec | Phase F (분기 1) |
| 미통과 → 정식 negative result + ablation | Phase F (분기 2) |
| 정식 보고서 + Architect verification | Phase F (verifier 에이전트) |

---

## 7. 리스크

| 위험 | 가능성 | 완화 |
|---|---|---|
| 5m 풀 stack 표본 부족 (D9: 30-150 trades/년) | 중 | 5m 5년 → 약 525,000 bar, 메타라벨러 확률 임계 0.6 시 충분 가능. 부족 시 status=insufficient_sample |
| 4h 풀 stack 표본 부족 (D5: 1-3 trades/년) | 높음 | 표본 부족이 정상 결과. negative result 일부로 기록 |
| top-10 alt 5m 5년 fetch 시간/디스크 | 중 | 사용자 사전 사이즈 안내 (~3-5GB), Phase D 게이트 명시 |
| UBAI 정의 변경 시 룰 #5 회귀 | 낮음 | 기존 cross_sectional_rs fallback 유지, 신규 fetcher 는 옵션 |
| variant_registry 사후 변경 압력 | 중 | sha256 freeze + Guardrails Must NOT 명시 + 테스트로 가드 |
| 워크트리 외 작업 위험 | 중 | 모든 task 에 `cd $WORKTREE` 또는 절대 경로 명시 |

---

## 8. 사용자 직접 작업 항목 (worker 가 못 함)

1. **alt 5m 5년 fetch** (Phase D 진입 게이트, 60-120분)
   ```
   cd D:/project/quantum-trader-agent/.worktree/000185-iranyi-12rules-5m
   python scripts/fetch_alt_universe_ohlcv.py --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,DOGEUSDT,LINKUSDT,ATOMUSDT --freq 5m --start 2020-01-01 --end 2025-12-31
   ```
2. **UBAI 인덱스 fetch** (Upbit API rate limit 고려)
   ```
   python -m src.data_lake.ubai_index --start 2020-01-01 --end 2025-12-31 --out lake/ubai_index.parquet
   ```
3. **git commit / push** (CLAUDE.md 행동 규칙 — 자동 커밋 금지)

---

## 9. 본 PR 의 의의

R4 (BTC 4h #173) + R6 (BTC 1h #199) 가 paper 30일 운영 중. 본 이슈는 그 옆에 새 sleeve (vwma_iranyi_v3 5m) 후보를 추가하기 위한 *사전등록 검증*. 게이트 미달 시 *모든 룰 stack 가설 자체* 가 거절된 negative result 로 정식 기록되어 #147 의 mhr 27.5% / B5 mhr 42.3% 결과와 합쳐 "Iranyi 식 진입 룰은 BTC 단일 또는 단일 TF 에서는 게이트 미달" 이라는 결론을 강화한다. 통과 시 R4+R6 옆 3번째 sleeve 후보로 격상.

---

## 작업 내역
<!-- /remind-issue 와 작업 진행 시 여기에 누적 -->

### 2026-05-05 (team-plan finalized)

**현황**: 0/7 AC 완료. team `iranyi-12rules-185` (3 executors) 가동.
**완료된 항목**: (없음 — Phase A 진입 직전)
**미완료 항목**: 7개 (Phase A~F)
**변경 파일**: 0 (계획 단계, 본 01_plan.md 만 갱신)

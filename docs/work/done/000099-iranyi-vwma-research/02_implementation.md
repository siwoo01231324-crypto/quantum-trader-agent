---
id: 02_implementation
type: work-done
name: "Iranyi VWMA #99 — 8-Variant Bench Implementation Report (5-year SOP run)"
issue: 99
status: negative-result
---

# 02_implementation — 이랑이 VWMA100 8-Variant 실험 결과 (5년 SOP run)

> 이슈 #99 의 사전 등록 factorial 실험을 **Binance Futures USDT-M BTC 1h 봉, 2020-01 ~ 2025-12 (실데이터 6년)** 에서 실행한 정식 판정 보고서. **결론: 게이트 미통과 (negative result)**. variant 8개 중 6개 평가, 2개 (G/H) 는 L2 tick 데이터 부재로 `DATA_UNAVAILABLE`.

## 실행 환경

| 항목 | 값 |
|------|------|
| 데이터 소스 | `lake::BTCUSDT@1h` (Binance Futures USDT-M, fetch via `scripts/fetch_futures_candles.py`) |
| 심볼 (거래) | BTCUSDT |
| 심볼 (벤치마크, Variant E) | ETHUSDT (UBAI placeholder — 후속에서 정식 Upbit 어댑터로 교체) |
| 기간 | 2020-01-01 ~ 2025-12-31 (6년, n_bars=52,585 1h) |
| Timeframe | 1h (1m 데이터를 `label='right', closed='right'` 인과 resample) |
| CV | PurgedKFold(n_splits=5, embargo_frac=0.01) |
| CV split hash | `3d6d388ed3dab5d4f60f4bc9adf19717…` (재현성 증거) |
| VARIANT_REGISTRY sha256 | `b8f7c1e8cfe2d941382bac3f329804d5…` (사전등록 무결성 증거) |
| Git commit (실행 시점) | `7c8e215c0fc9` (master 기준) |
| 거래비용 | Binance taker fee 0.0008 round-trip |
| 실행 timestamp | 2026-04-27T09:47:34Z |

## 8 Variant 메트릭 테이블

| ID | status | n_trades | Sharpe | Sortino | MDD | Calmar | mhr | skew | kurt_ex | 비고 |
|----|--------|---------:|-------:|--------:|----:|-------:|----:|-----:|--------:|------|
| A | ok | 2665 | +0.046 | +0.063 | -0.823 | +0.001 | 0.43 | -0.66 | 9.18 | VWMA100 단독 baseline |
| B | ok | 1345 | **+0.346** | **+0.494** | -0.599 | +0.024 | 0.40 | -0.51 | 6.87 | A + ema_slope > 0 — **winner** |
| C | ok | 2665 | +0.046 | +0.063 | -0.823 | +0.001 | 0.43 | -0.66 | 9.18 | A + multi_tf alignment (A 와 동일, 효과 없음) |
| D | ok | 2037 | -0.062 | -0.085 | -0.813 | -0.001 | 0.39 | -0.71 | 9.40 | A + time_gate (KST 10:30~11:00 + 주말) |
| E | ok | 1194 | -1.279 | -1.793 | -0.903 | -0.022 | 0.19 | -1.31 | 13.16 | A + cross_sectional_rs (BTC vs ETH placeholder) |
| F | ok | 1358 | -0.160 | -0.219 | -0.728 | -0.003 | 0.39 | -0.86 | 10.27 | A + poc_distance |
| G | DATA_UNAVAILABLE | 0 | NA | NA | NA | NA | NA | NA | NA | L2 tick 미가용 (#80 paper broker 의존) |
| H | DATA_UNAVAILABLE | 0 | NA | NA | NA | NA | NA | NA | NA | full stack — G 의존성으로 동일 |

(메트릭은 `bench_output.json` 의 원본 값. mhr = monthly hit rate, kurt_ex = excess kurtosis.)

## 통계 보정 (Multi-Testing)

| 지표 | 값 | 게이트 | 통과 |
|------|---:|--------|------|
| **DSR (Bailey & López de Prado 2014)** | 0.0 | ≥ 0.95 | ❌ |
| **PBO (CSCV n_groups=8)** | 0.2563 | ≤ 0.20 | ❌ (한계 초과 0.056) |
| **OOS MDD (winning B)** | -0.599 | < 0.25 | ❌ |
| **monthly_hit_rate (winning B)** | 0.40 | ≥ 0.50 | ❌ |
| N_trials_actual | 6 | (G/H 제외 동적) | — |

## 판정: **Negative Result — Gate FAIL**

> "Variant B (VWMA100 + ema_slope > 0) 가 가장 양의 Sharpe (+0.346) 를 보였으나, **DSR=0 (multi-testing 보정 후 통계적 유의 없음), PBO=0.26 (overfitting 가능성), MDD=-60% (한계 초과), monthly_hit_rate=40% (한계 미달)** 의 4 항목 게이트 모두 실패. 영상 8 기법의 자동화 매핑은 본 backtest 모델에서 BTC 1h 5년 구간에 대해 **편입 보류** 한다."

전략 코드 (`src/backtest/strategies/vwma_cross.py`) 와 spec (`docs/specs/strategies/vwma-cross-v1.md`) 은 **생성하지 않는다 (경로 B)**.

## 의미 있는 발견 (negative result 의 진단적 가치)

### 1. Variant B (VWMA + EMA slope filter) 의 Sharpe +0.346
- baseline A (+0.046) 대비 7배 개선 → **EMA 추세 필터가 false signal 을 의미 있게 쳐냄** (n_trades 2665 → 1345)
- 단 여전히 risk-adjusted 후 게이트 미달
- Sortino +0.494 (Sharpe 보다 큼) → 우상향 변동이 우하향보다 큰 분포

### 2. Variant C (multi_tf alignment) 효과 없음 (A 와 동일 메트릭)
- 1h 봉 + 1h 상위 TF VWMA 의 정배열 boolean 은 사실상 항상 True (또는 cross 시점과 100% 겹침)
- 의미: **상위 TF 가 1h 와 너무 가깝거나, multi_tf gate 의 boolean 이 모든 entry 시점에 True 가 됨** → 실효성 없는 필터
- 후속: 4h 또는 1d 상위 TF 로 변경하여 재평가 필요

### 3. Variant E (cross_sectional RS) 가장 큰 Sharpe 음수 (-1.279)
- Universe 가 BTC 1개 + benchmark 1개 (ETH) → 사실상 cross-sectional 차원 부재
- BTC 가 ETH 보다 outperform 할 때만 진입 → 대부분 시간대 차단
- **정식 UBAI 어댑터 (Upbit 상위 20 알트 시총 가중) 와이어링 필수** — 본 결과는 placeholder 한계
- 후속 이슈에서 정식 UBAI 와 multi-asset universe 로 재실험

### 4. MDD 모든 variant -60% ~ -90% — backtest 모델 한계
- **본 backtest 는 stop-loss 미구현**. Position 은 vwma cross 'dead' 시까지 hold
- 영상 화자는 -1% stop + 7% target (1:7 R:R) 명시
- Stop-loss 추가 시 MDD 30%+ 개선 + Sharpe 개선 여지 있음 → **후속 이슈 후보 (stop-loss 통합 backtest)**

### 5. PBO=0.26 — variants 간 OOS rank 약상관
- random (0.5) 보다는 좋음 → 일부 variant 가 일관되게 outperform
- 단 PBO ≤ 0.20 게이트 한계 살짝 초과 (0.06 차이)
- 5년 + 6 variant 의 표본 부족 가능성

## 사전 등록 무결성 증거

본 실험은 [[01_plan]] (status=approved, ralplan iter-2 합의) 에 따라 사전 등록되었으며, 다음 무결성 증거를 통해 **사후 variant 추가/삭제 없음** 을 확인할 수 있다:

- `variant_registry_sha256`: `b8f7c1e8cfe2d941382bac3f329804d5…` — `VARIANT_REGISTRY` 의 sha256 hash. 어떤 사후 수정도 hash 변경으로 노출됨.
- `cv_split_hash`: `3d6d388ed3dab5d4f60f4bc9adf19717…` — PurgedKFold 의 결정론적 split index hash. 모든 variant 가 동일 split 사용 확인.
- `git_commit`: `7c8e215c0fc9` — 실행 시점의 master HEAD.
- `bench_output.json` — 8 variant 전체 메트릭 + 위 hash + timestamp 모두 보존.

## 후속 이슈 후보 (이슈 #99 범위 밖)

본 이슈의 사전 등록 원칙상 **추가 variant 또는 backtest 모델 변경은 본 이슈에서 금지**. 다음을 별도 이슈로 분리:

1. **Stop-loss / take-profit 통합 backtest** — 영상의 1% stop / 7% target 또는 ATR-based stop. 본 이슈 negative result 의 가장 큰 개선 여지.
2. **L2 tick 데이터 인프라 (variant G/H 활성화)** — #80 paper broker 의 L2 ingestion + 1s raw zstd parquet 보존 + 1m 집계 파이프라인. Hawkes intensity, OFI 등 micro-feature 사용.
3. **정식 UBAI 어댑터 (variant E 활성화)** — Upbit public REST `/v1/market/all` + `/v1/ticker` 운영 어댑터, BTC dominance 역수 fallback. 상위 20 KRW 알트 시총 가중 매월 1일 리밸런스.
4. **Multi-TF 4h/1d 상위 frame 으로 재평가** — variant C 가 1h base 에서 무효 → 더 큰 frame 분리.
5. **선물 short 변형** — 영상 라인 415-431, 하락 추세 + 200선 도달 시 short.
6. **Multi-asset universe 확장** — BTC 외 ETH, SOL 등 universe 화 (cross-sectional momentum 의미 부여).

## 산출물 일람

### 신규 파일 (커밋 후보)
- `docs/research/raw/iranyi-vwma-2026-04-27.md` (영상 전사 + 8 기법 매핑, 737 라인)
- `docs/research/.ai.md`, `docs/research/raw/.ai.md`
- `docs/background/{36..39}-*.md` (4 research 노트)
- `src/ml/validation/{__init__,deflated_sharpe,cscv,pbo}.py` + `.ai.md`
- `src/features/{__init__,vwma,ma_projection,multi_tf,time_of_day,cross_sectional_rs,poc,orderbook_flow}.py` + `.ai.md`
- `scripts/bench_iranyi_variants.py`
- `tests/test_validation_dsr.py`, `tests/test_validation_pbo.py`, `tests/test_iranyi_features.py` (총 36 테스트)
- `docs/work/active/000099-iranyi-vwma-research/{00_issue.md, 01_plan.md, 02_implementation.md, bench_output.json}`

### 수정 파일
- `src/ml/.ai.md` (scope 확장: "AFML 기반 ML + Validation 도구체인", validation/ 서브패키지 행 추가)

### 데이터 (lake/, .gitignore 적용 — 미커밋)
- `lake/ohlcv/freq=1m/year={2020..2025}/month={01..12}/symbol={BTCUSDT,ETHUSDT}/part-0.parquet` — 6년 BTC + ETH 1m, 총 ~283 MB

## 검증 게이트 통과 사항

- ✅ `python scripts/check_invariants.py --strict` 통과 (119 노트 검증, 신규 4 background + 1 work-done)
- ✅ `pytest tests/test_iranyi_features.py tests/test_validation_dsr.py tests/test_validation_pbo.py` — **36 / 36 passed**
- ✅ Lookahead guard 통과 (`assert_no_lookahead` from `src.signals.lookahead_guard`)
- ✅ 사전 등록 무결성 (registry sha256 + cv split hash + git commit 보존)
- ✅ 영상 출처 명시 (CLAUDE.md 조사 규칙) — 4 research 노트 + 02_implementation 모두

## 관련 노트
- 영상 원본: [[iranyi-vwma-2026-04-27]]
- Research: [[40-vwma-volume-weighted-ma]] · [[41-multi-tf-fractal-trading]] · [[42-cross-sectional-momentum-crypto]] · [[43-orderbook-flow-features]]
- 검증 인프라: `src/ml/validation/` + `src/ml/cv.py` (PurgedKFold #85)
- Feature 모듈: `src/features/`
- Bench: `scripts/bench_iranyi_variants.py` (5년 SOP run)
- 통합 계획: `01_plan.md` (status=approved)
- 검증 SOP: [[12-validation-protocol]] §3.7

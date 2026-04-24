# chore: 팩터 점증 계산 + momo-btc-v2 훅 마이그레이션

## 목적
#71 에서 도입한 엔진 팩터 precompute 경로의 O(N²) 성능 병목 해소 + `momo-btc-v2` 를 `required_factors` 훅 소비 방식으로 마이그레이션.

## 배경
- `src/backtest/engine.py` 는 바마다 `history = ohlcv.iloc[:i+1]` 를 `signals.registry.compute()` 에 전달 → 호출 N회 × 호출당 O(N) = O(N²)
- `src/signals/rsi.py::compute_rsi` 는 Python-level `for i in range(period+1, len(close))` 루프
- 실측 (dev 머신): n=500 → 5.7s / n=1000 → 15.5s / n=2000 → 57.8s / n=4000 → 156s. 70k bar 외삽 ~13h.
- 상세: `docs/work/active/000071-alpha-factors/02_perf_benchmark.md`
- `momo-btc-v2` 는 현재 엔진 훅을 쓰지 않고 전략 내부에서 직접 `compute_rsi()` 를 호출 → 엔진 최적화 효과를 받지 못함.

## 완료 기준

### Part A — 엔진 증분 계산
- [ ] 70k-bar 15m 단일 심볼 백테스트 `required_factors=["rsi"]` wall time < 60s
- [ ] 5개 팩터 동시 선언 시 wall time < 120s
- [ ] 기존 룩어헤드 가드 테스트 전부 그대로 통과
- [ ] 결과 값이 현행 full-recompute 경로와 bit-identical (회귀 테스트)

### Part B — momo-btc-v2 훅 마이그레이션
- [ ] `momo_btc_v2.MomoBtcV2` 에 `required_factors = ["rsi"]` 선언 추가
- [ ] `on_bar()` 가 `context["factors"]["rsi"]` 에서 RSI 시리즈 소비 (자체 `compute_rsi` 호출 제거)
- [ ] 마이그레이션 전후 동일 OHLCV 데이터로 백테스트 시 equity curve bit-identical
- [ ] `docs/specs/strategies/momo-btc-v2.md` 에 "훅 소비" 섹션 추가 (`required_factors` 필드 명시)
- [ ] `tests/backtest/` 에 마이그레이션 회귀 테스트 1건 (구 전략 vs 신 전략 결과 동일 검증)
- [ ] `src/backtest/.ai.md` · `src/backtest/strategies/.ai.md` 의 "프로덕션 전략 훅 소비 금지" 경고 제거

## 구현 플랜
1. **엔진 최적화 선행** — 본 단계가 bit-identical 회귀 테스트 통과 전까지 전략 마이그레이션 금지.
2. 엔진에 팩터 상태 캐시 추가 (`_factor_state: dict[str, Any]`) 또는 선계산+슬라이싱 경로
3. Wilder 계열(RSI, ATR) — 점증 업데이트 공식 (`avg = avg*(n-1)/n + new/n`)
4. rolling 계열(SMA, Bollinger, RealizedVol) — `.rolling()` 결과 캐시 + 슬라이스
5. ewm 계열(MACD) — 상태 보존 EMA
6. 회귀 테스트: 전 팩터에 대해 incremental 결과 == full-recompute 결과 bit-equal
7. **(Part A 검증 후)** `momo-btc-v2` 훅 마이그레이션 — 클래스 속성 + `on_bar` 시그니처 조정 (5~10 줄 변경 예상)
8. 마이그레이션 회귀 테스트 — 구 전략(compute_rsi 직접 호출) vs 신 전략(훅 소비) 동일 데이터 백테스트 비교

## 제약
- 본 이슈는 `momo-btc-v2` 마이그레이션까지 포함한다 (원래 후속 이슈로 분리 예정이었으나 변경량이 작아 합침).
- **엔진 최적화 단계가 bit-identical 회귀 테스트를 통과한 후에만 전략 마이그레이션 단계 진행.**
- `src/backtest/.ai.md` 의 "프로덕션 전략 훅 소비 금지" 경고 문구는 본 이슈 머지 시 제거한다.

## 선행
- #71 머지 (팩터 프레임워크) — 완료됨

## 개발 체크리스트
- [ ] 해당 디렉토리 .ai.md 최신화 (`src/backtest/.ai.md`, `src/backtest/strategies/.ai.md`, `src/signals/.ai.md`)

---

## 🔍 특허 리서치 (#84) 보강

팩터 점증 계산 영역에 특허 리서치에서 도출된 **3 개 차용 제안** 이 연관된다. 증분 계산 구현 시 인접 관심사로 함께 정리.

### 1. FactorSpec long_window · short_window 분리 (SP-1)
- **출처**: `docs/background/33-patents-factor-models.md` §2 💎
- **특허**: State Street US20210224700A1 (deep-SARIMAX 이중 시퀀스 구조 차용)
- **내용**:

> **제안 이름**: LSTM 장기 패턴 + 커널 단기 외생변수 이중 입력 구조를 `src/signals/` 팩터에 적용
>
> **적용 대상 파일/함수**: `src/signals/registry.py::compute()` 및 신규 `src/signals/lstm_factor.py`
>
> **접목 방법**: 현재 `registry.py::compute()`는 단일 OHLCV 딕셔너리를 팩터 함수에 그대로 전달한다. State Street 특허의 핵심 구조—"장기 시퀀스(내부 패턴용)"와 "단기 시퀀스(외생 팩터용)"를 분리하여 각각 다른 모델에 공급—를 차용하면, `FactorSpec`에 `long_window`(LSTM 내부 패턴용, 예: 252봉)와 `short_window`(외생 변수용, 예: 20봉)를 추가 필드로 선언하고 `compute()`에서 자동으로 슬라이싱하여 전달할 수 있다. 이로써 팩터별 입력 길이를 레지스트리 수준에서 강제할 수 있어 룩어헤드 방지 `assert_no_lookahead()`의 입력 일관성이 높아진다.
>
> **기대 효과**: 팩터 함수가 자체적으로 시퀀스 분할을 처리하지 않아도 됨 → 팩터 코드 단순화. `lookahead_guard.py`의 `_slice_inputs()`와 연동 시 단기 시퀀스 경계가 명확히 정의되어 데이터 누출 감지 정확도 향상.
>
> **저비용 검증 경로**: `FactorSpec`에 `long_window: int = 252`, `short_window: int = 20` 필드 추가 → `compute()`에서 슬라이싱 로직 5줄 추가 → 기존 테스트 통과 여부 확인.

---

### 2. 3단계 팩터 품질 게이트 — 룩어헤드 → ICIR 필터 → 상관 중복 제거 (SP-3)
- **출처**: `docs/background/33-patents-factor-models.md` §4 💎
- **특허**: Axioma US20130332391A1 (**포기 특허**, 자유 실시)
- **내용**:

> **제안 이름**: 팩터 파이프라인에 순차 품질 필터(Tiered Quality Gate) 도입
>
> **적용 대상 파일/함수**: `src/signals/registry.py::register()` 데코레이터 및 신규 `src/signals/pipeline.py::run_pipeline()`
>
> **접목 방법**: 현재 `registry.py`는 팩터를 등록하고 `compute()`로 단일 호출한다. Axioma 특허의 순차 최적화 구조를 차용하면, 팩터 파이프라인을 3단계 품질 게이트로 구성할 수 있다: **Stage 1** — 룩어헤드 검증 (`assert_no_lookahead()`), **Stage 2** — IC 임계값 필터 (ICIR ≥ 0.3 미만 팩터 비활성화), **Stage 3** — 팩터간 상관 중복 제거 (pairwise |correlation| > 0.7이면 IC 낮은 쪽 제거). `run_pipeline(factor_names, ohlcv, min_icir=0.3, max_corr=0.7)` 형태로 호출하면 매 리밸런싱 주기마다 활성 팩터 세트를 동적으로 결정.
>
> **기대 효과**: 팩터 품질 저하(IC 감소, 팩터 붐비기)를 레지스트리 수준에서 자동 감지. 현재 수동 확인에 의존하는 팩터 유효성 관리를 자동화하여 알파 소멸(alpha decay) 조기 탐지 가능.
>
> **저비용 검증 경로**: `registry.py` 위에 `pipeline.py` 모듈을 신규 생성, `run_pipeline()` 구현 후 기존 RSI/MACD/ATR 팩터에 적용하여 각 Stage 통과 여부 확인. 예상 구현: 1일.

> 설계 조율: 본 게이트는 **팩터 생성 단계(0차)** 에 위치. #85 메타라벨링(신호 2차 필터) 와는 단계 분리.

---

### 3. BTC dominance 레짐 팩터 (P2)
- **출처**: `docs/background/31-uprich-patent-analysis.md` §4 💎 #2
- **특허**: 업리치 KR 10-2024-0114873 청구항 1-(c) BTC dominance 개념 차용 (수식 직접 복제 금지, 이진 신호로 단순화하여 회피)
- **내용**:

> **적용 대상**: `src/` (신규 모듈 `src/factors/btc_dominance.py`) + [[30-market-regime-detection]] Phase 1 Rule-based 분류기
>
> **접목 방법**: [[30-market-regime-detection]] §5.1 의 `RegimeClassifier.classify()` 는 현재 `ewma_sigma_20`, `adx_14`, `vol_percentile_252d` 만 입력으로 받는다. 업리치 특허의 `C = a × Bdominance × T` 개념을 변형해 BTC dominance 를 **레짐 입력 축** 으로 추가한다.
>
> 구체적으로 `src/factors/btc_dominance.py` 에 다음을 구현한다:
>
> ```python
> def btc_dominance_signal(
>     btc_mcap: float,
>     total_mcap: float,
>     dominance_ma20: float,
> ) -> float:
>     """BTC dominance 추세 방향 [-1, 1].
>     dominance > ma20: BTC 강세 (+), < ma20: 알트 강세 (-)
>     """
>     dominance = btc_mcap / total_mcap if total_mcap > 0 else 0.5
>     return float(np.sign(dominance - dominance_ma20))
> ```
>
> 이 신호를 `RegimeClassifier` 의 `classify()` 메서드에 `btc_dominance_trend` 필드로 추가해, BTC dominance 상승 국면에서는 모멘텀 전략 가중을 높이고 하락 국면(알트 시즌)에서는 알트코인 비중을 제한하는 의사결정 매트릭스를 확장한다. 특허의 `C = a × Bdominance × T` 수식 그대로가 아니라 **이진 부호 신호**로 단순화하므로 구성요소 (c) 를 침해하지 않는다.
>
> **기대 효과**: 알트 시즌 vs BTC 지배 국면에서 포트폴리오 내 자산 배분 자동 조정. [[30-market-regime-detection]] §4 의사결정 매트릭스에 dominance 축 추가로 체제 분류 세분화.
>
> **저비용 검증 경로**: BTC dominance 과거 데이터(CoinGecko public API) 60일 다운로드 → dominance 상승/하락 구간에서 BTCUSDT 모멘텀 전략의 Sharpe 비교 스크래치 노트북 1개.

---

### 관련 연구 노트
- [[33-patents-factor-models]]
- [[31-uprich-patent-analysis]]
- [[30-market-regime-detection]]

### 연결 이슈
- #84 특허 리서치
- #85 메타라벨링 — 본 품질 게이트와 단계 분리 주의



## 작업 내역

- **2026-04-24**: 이슈 범위 확장 — Part A(엔진 증분) + Part B(momo-btc-v2 훅 마이그레이션) 합침
- **2026-04-24**: 플랜 v2 합의 — Planner/Architect/Critic consensus (ralplan), Architect 7 revisions + Critic 4 Must-Fix + 3 Should-Fix 반영
- **2026-04-24**: 구현 완료 (Team+Ralph 모드, 6 user stories)
  - US-001: `src/backtest/engine.py` 루프 전 precompute + `.iloc[:i+1]` 슬라이싱 (+24/-6 줄). `src/signals/registry.py` `FactorSpec.causal: bool = True` 필드.
  - US-002: `tests/backtest/test_backtest_factor_integration.py` `_FactorProbeAllBarsStrategy` + `test_precompute_bit_identical_all_bars` (500바 × 5팩터 = 2500 포인트, `check_exact=True`).
  - US-003: `test_5factor_perf` 추가. 실측 wall time — 단일 RSI 70k-bar **14.96s** (<60s), 5팩터 **14.34s** (<120s).
  - US-004: `src/backtest/strategies/momo_btc_v2.py` `required_factors=["rsi"]` 선언 + `context["factors"]["rsi"]` 소비. `compute_rsi` import 제거 (detect_divergence 유지).
  - US-005: `tests/backtest/test_momo_btc_v2_migration.py` 신규. `_LegacyMomoBtcV2` in-file 스냅샷 + parametrize 회귀 (3 sizing_mode × {equity_curve, trades} = 6 테스트 전부 bit-identical PASS).
  - US-006: `src/backtest/.ai.md` 경고 교체, `src/signals/.ai.md` FactorSpec 필드 문서화, `docs/specs/strategies/momo-btc-v2.md` "훅 소비" 섹션 추가.
- **2026-04-24**: simplify 리뷰 반영 — `momo_btc_v2.py` 화살표 주석 제거, `engine.py` `AssertionError` → `ValueError` (설정 오류는 의미상 Value).
- **2026-04-24**: Architect 최종 검증 — AC 10/10 PASS + 체크리스트 7/7 PASS. APPROVE.

### 성과 요약
| 항목 | 결과 |
|------|------|
| 엔진 복잡도 | O(N²) → **O(N)** |
| 70k-bar RSI | ~13시간 → **14.96초** (3100배) |
| 70k-bar 5팩터 | ~13시간+ → **14.34초** |
| bit-identical 검증 | **2500 포인트** PASS (`check_exact=True`, atol/rtol 금지) |
| 마이그 회귀 | **6/6 PASS** (equity + trades, 3 sizing mode) |
| 팩터 함수 수정 | **0 줄** (Principle #1 완전 준수) |
| 전체 회귀 | 89 pass / 6 skipped (pandas-ta 선택적) |

### 변경 파일 (9개 수정 + 1개 신규 = 135줄)
- `src/backtest/engine.py`, `src/backtest/strategies/momo_btc_v2.py`, `src/signals/registry.py`
- `tests/backtest/test_backtest_factor_integration.py`, `tests/backtest/test_momo_btc_v2.py`
- **신규**: `tests/backtest/test_momo_btc_v2_migration.py`
- `src/backtest/.ai.md`, `src/signals/.ai.md`, `docs/specs/strategies/momo-btc-v2.md`, `tests/.ai.md`

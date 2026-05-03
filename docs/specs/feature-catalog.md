---
type: spec-architecture
id: feature-catalog
name: Feature Catalog — 활성화 가능한 옵션 색인
owner: siwoo
status: draft
tags: [discoverability]
---

# Feature Catalog — 활성화 가능한 옵션 색인

> **이 문서의 목적**: 프로젝트에 추가된 **모든 opt-in 기능**을 한 곳에서 검색 가능하게 한다. 새 기능을 추가했으나 기본 비활성 (`Optional=None`, `bool=False` 등) 으로 숨어 있으면 6개월 뒤 잊혀진다. 이 카탈로그에 반드시 등재해야 한다.
>
> **갱신 규칙**: 새 구현 이슈 머지 시 이 파일에 **기능 행** 추가. 누락 시 리뷰어는 PR 차단. `/fi` 플로우에 이 파일 갱신 체크 포함 예정 (#86 에서).
>
> **관련 문서**: `docs/specs/risk-rule-dsl.md` §8.1 (DSL 필드 상세), `docs/onboarding/getting-started.md` (일상 워크플로우).

## 검색 방법

1. **Ctrl+F "분야"** — Risk · Sizing · Universe · Portfolio · Strategy · Signals · Backtest · Execution · Observability
2. **Ctrl+F 이슈번호** — 예: `#87`, `#70`, `#69`
3. **Ctrl+F 함수명** — 예: `consensus_kelly`, `historical_cvar_levels`

---

## 1. Risk (`src/risk/`)

### 1.1 Portfolio Risk (#70, v2)

| 기능 | 활성화 | 기본값 | 테스트 |
|---|---|---|---|
| Historical CVaR(α=0.975) | `per_portfolio_risk.max_cvar_pct` (YAML) | OFF | `tests/test_portfolio_risk.py` |
| 평균 pairwise ρ 상한 | `per_portfolio_risk.max_corr_avg` | OFF | `tests/test_portfolio_risk.py` |
| Meucci ENB ratio 하한 | `per_portfolio_risk.min_enb_ratio` | OFF | `tests/test_portfolio_risk.py` |
| LW shrinkage Σ 추정 | `risk.portfolio.compute_portfolio_risk_from_df()` | 항상 사용 | `tests/test_portfolio_risk.py` |

### 1.2 #87 특허 차용 확장

| 기능 | 활성화 | 기본값 | 파일 | 테스트 |
|---|---|---|---|---|
| 다중 α CVaR 계층 (warn / reduce / halt) | `per_portfolio_risk.cvar_levels: [[α, label], ...]` (YAML) | OFF | `src/risk/portfolio.py::historical_cvar_levels`, `src/risk/dsl.py` | `tests/test_cvar_levels.py` |
| 극단 공포 차단 (가격 기반 fear_greed_proxy) | `per_portfolio_risk.extreme_fear_block: true` (+ `extreme_fear_threshold: 0.2`) | OFF | `src/portfolio/orchestrator.py::compute_fear_greed_proxy`, `src/risk/dsl.py::Snapshot.fear_greed_proxy` | `tests/test_fear_greed_proxy.py` |

활성화 YAML 예시는 `policies/conservative.yaml` 의 주석 블록 또는 `docs/specs/risk-rule-dsl.md` §8.1 참조.

---

## 2. Position Sizing (`src/risk/sizing.py`, `src/risk/position_sizer.py`)

### 2.1 Core (#69)

| 함수 | 용도 | 참조 |
|---|---|---|
| `kelly_binary`, `kelly_continuous` | 정수 Kelly (이산/연속) | `docs/background/20-position-sizing.md` |
| `fractional_kelly(full, k=0.5)` | Half-Kelly 등 축소 Kelly | 〃 |
| `vol_target(sigma, target_annual, periods)` | 변동성 타겟팅 사이저 | 〃 |
| `ewma_sigma(returns, lam=0.94)` | RiskMetrics EWMA σ | 〃 |

### 2.2 #87 특허 차용 확장

| 함수 | 활성화 | 기본값 | 테스트 |
|---|---|---|---|
| `user_risk_vol_target(risk_score, vol_floor=0.05, vol_ceil=0.20)` | 호출자가 `vol_target()` 대신 직접 호출 | N/A (함수) | `tests/test_sizing_user_risk.py` |
| `consensus_kelly(full_kelly, signal_agreement, k_base=0.5, k_max=0.75)` | `MomoBtcV2(sizing_mode="half-kelly", use_consensus_kelly=True, signal_agreement=...)` | OFF | `tests/test_consensus_kelly.py` |
| `equal_risk_contribution_convex(cov, target_contrib=None)` | 포트폴리오 사이저 경로에서 직접 호출 (scipy SLSQP, IVP fallback) | N/A (함수) | `tests/test_position_sizer_erc.py` |
| `hrp_with_clustering(returns, k_clusters=None)` | 직접 호출. `k_clusters=None` 시 단일 HRP fallback | `None` = 단일 HRP | `tests/test_position_sizer_hrp.py` |

**중요**: `user_risk_vol_target` 과 `consensus_kelly` 둘 다 `fractional_kelly`/`vol_target` 을 내부 위임하므로 기존 clamp `[0, 1]` 동작 보존.

---

## 3. Universe (`src/universe/`)

### 3.1 Stability Grade (#87 P3)

| 기능 | 활성화 | 기본값 | 파일 | 테스트 |
|---|---|---|---|---|
| 알트코인 A~F 안정성 등급 | `StabilityGrade.grade(mcap, vol_30d, dev_activity)` 직접 호출 | N/A (pure function) | `src/universe/stability_grade.py` | `tests/test_stability_grade.py` |
| dev_activity=None 시 가중치 자동 재분배 | 자동 (`{mcap: 0.5, volume: 0.5}`) | 자동 활성 | 〃 | 〃 |

**Out-of-scope (후속 이슈)**: DSL 배선 (`per_portfolio_risk.stability_grade_min: D`) — 현재 `src/risk/dsl.py` 에 스키마 없음. CoinGecko 어댑터 (데이터 수집) — 별도 이슈.

---

## 4. Strategy (`src/backtest/strategies/`)

### 4.1 MomoBtcV2 (#67, #69, #87)

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `sizing_mode` | `"full"` | `full` / `half-kelly` / `vol-target` |
| `sizing_lookback` | 60 | kelly/vol-target 계산 윈도우 |
| `kelly_k` | 0.5 | Half-Kelly 기본 축소율 |
| `target_annual` | 0.20 | vol-target 타겟 연율화 σ |
| `periods_per_year` | `365*96` | BTC perp 15m bar 연율화 |
| `ewma_lam` | 0.94 | EWMA σ λ |
| **`use_consensus_kelly`** (#87) | `False` | True 시 `consensus_kelly` 위임 |
| **`signal_agreement`** (#87) | `0.0` | `use_consensus_kelly=True` 일 때 소비 |
| **`consensus_k_base`** (#87) | `0.5` | agreement=0 에서 k |
| **`consensus_k_max`** (#87) | `0.75` | agreement=1 에서 k |

---

## 5. Signals / Factors (`src/signals/`, #71)

팩터 레지스트리 + 룩어헤드 가드. 상세: `src/signals/.ai.md`, `docs/specs/signals/`.

- `@register(name, inputs=[...])` 데코레이터로 팩터 등록 → `compute(name, **inputs)` 호출.
- `required_factors: list[str]` 를 Strategy 클래스에 선언 → 엔진이 precompute 후 `context["factors"][name]` 주입.
- **성능 주의**: 현재 엔진 precompute 는 O(N²) — 70k bar 외삽 ~13h. follow-up #81 에서 incremental 해결 전까지 프로덕션 전략에서 `required_factors` 훅 사용 금지.

### 5.1 오더플로우·ICT 시그널 사전 등록 (#145 — 구현은 후속 이슈)

> 본 섹션은 **research 단계 사전 등록**이다. 코드 구현·테스트·배선은 후속 이슈에서 수행한다. 학술 근거 및 데이터 요건 상세는 `docs/background/37-orderflow-microstructure-signals.md` 참조.

| 팩터 슬롯 | 예정 파일 | 학술 근거 | 데이터 요건 | Sleeve B 역할 | 구현 이슈 |
|-----------|----------|----------|------------|--------------|----------|
| `cvd_divergence` (CVD 다이버전스) | `src/signals/cvd.py` | Kyle (1985) λ, Cont et al. (2014) OFI | Binance aggTrade (`m` 필드 필수) | 추세 확인/반전 필터 | TBD (후속) |
| `vpin` (Volume-sync. Prob. of Informed Trading) | `src/signals/vpin.py` | Easley, López de Prado, O'Hara (2012) | Binance aggTrade + BVC 분류기 | 독성 플로우 게이트 (꼬리 손실 방어) | TBD (후속) |
| `liq_sweep_reversal` (유동성 스윕 역진입) | `src/signals/liq_sweep.py` | Osler (2003) FX 스탑 클러스터 실증 | OHLCV + 거래량 (기존 인프라) | 저상관 단기 반전 알파 | TBD (후속) |
| `order_block_level` (오더 블록 지지/저항) | `src/signals/order_block.py` | Easley & O'Hara (1992) 순차 거래 모델 | OHLCV + 거래량 | 기관 누적 레벨 참조 | TBD (후속) |
| `fvg_fill` (FVG 채움 시그널) | `src/signals/fvg.py` | Roll (1984) 비드-애스크 바운스 (간접) | OHLCV (3봉) | 단기 평균회귀 필터 | TBD (후속) |

**중요 제약**:
- CVD, VPIN 은 Binance aggTrade fetcher 신규 구현 선행 필요. KIS 환경에서 CVD 불가, VPIN 근사만 가능.
- 학술 근거 낮은 팩터(FVG, Order Block, Breaker) 는 단독 신호 금지 — CVD/VPIN 확인 + [[35-meta-labeling-lopez-de-prado]] 2단계 구조 결합 필수.
- 전 팩터 `lag(1)` 디폴트 강제 (룩어헤드 방지, [[12-validation-protocol]] §2).

---

## 6. 활성화 흐름 요약

```
사용자 관점:
  1. 이 카탈로그 Ctrl+F 로 원하는 기능 찾기
  2. "활성화" 컬럼의 YAML 키 or 함수 시그니처 확인
  3. 테스트 파일 읽어 사용 예시 학습
  4. policies/*.yaml 의 주석 블록 해제 or 코드에서 직접 호출
```

## 7. Dormant Features — 구현됐지만 현재 호출 경로 없음

> 이슈는 머지되어 코드·테스트는 있으나 **프로덕션 호출 경로가 배선되지 않은 기능**. 라이브 운용 전에 반드시 활성화 이슈를 열어 배선해야 한다. 테스트에서만 검증되는 상태.

### 7.1 Orchestration / Execution

| 기능 | 상태 | 배선 차단 원인 | 활성화 이슈 (예정) |
|---|---|---|---|
| `StrategyOrchestrator` (#70, `src/portfolio/orchestrator.py`) | Dormant | 현재 `scripts/run_backtest.py` 는 MomoBtcV2 를 직접 호출하고 `StrategyOrchestrator` 경유 없음. `evaluate_order()` / `refresh_portfolio_risk()` 는 테스트에서만 실행됨. | #78 async 확장 + #80 라이브 루프 |
| **Engine `required_factors` 훅** (#71, `src/backtest/engine.py::run_backtest`) | Dormant | MomoBtcV2 가 `required_factors` 클래스 속성을 선언하지 않아 훅 우회. 플랜상 **#81 (팩터 점증 계산) 해결 전까지 프로덕션 전략에서 사용 금지** — 70k bar 외삽 13h. | #81 |
| `TWAPAlgo` / `VWAPAlgo` / `LimitAlgo` / `MarketAlgo` (#25, `src/execution/`) | Dormant | 현재 백테스트 엔진은 `bar.close * (1 ± slippage_pct)` 단순 체결. 실거래 브로커 라우터(`src/brokers/router.py`)에도 Algorithm 주입 경로 없음. | 실거래 통합 (#80 선행) |
| `KRXSingleAuctionHandler` (#25, `src/execution/krx_handler.py`) | Dormant | KRX 실거래 경로 미구현. 현행 유니버스 = BTCUSDT 단일 (암호화폐). | KRX 진출 이슈 |
| `src/ops/triggers.py` — `CVaRMaxTrigger`, `DrawdownTrigger`, `LatencyTrigger` (#27) | Dormant | 트리거 정의는 있으나 주기 스케줄러/실행 루프 없음. KillSwitch 는 `brokers/router.py` 에서 방어 목적으로만 생성 (trip 경로 미연결). | #80 라이브 루프 |
| `src/ops/cli.py` KillSwitch CLI (#27) | Partial | 커맨드 파싱은 동작하나 실제 트립 → 브로커 차단 체인이 미완. | 실거래 통합 |
| `src/tax/calculator.py` + `reporter.py` (#28) | Dormant | 세금 계산·연말 CSV 리포터 구현됐으나 실거래 체결 기록 입력 소스가 없음 (paper 이전 단계). | 실거래 + 연말 정산 시점 |
| `src/tax/calculator.py::is_major_shareholder` flag | Feature flag (default False) | 지분율 기반 중과세 경로. 활성 조건 발생 시 호출자가 명시적으로 주입. | 해당 사건 발생 시 |

### 7.2 Binance / KIS 브로커 (#68)

| 기능 | 상태 | 비고 |
|---|---|---|
| `BinanceAdapter` · `KISAdapter` 전체 REST/WS | Partial | 단위 테스트 + integration (`pytest -m integration`) 동작. **프로덕션 주문 루프에서 호출되는 경로는 없음** — `StrategyOrchestrator` 가 dormant 이기 때문. |
| `BinanceAdapter.hedge_mode` | Dormant flag | `ensure_position_mode()` 호출 시 설정되나 주문 경로에서 소비 여부 미확정. |
| `Reconciler` (#68, `src/brokers/binance/reconciler.py`) | Dormant | 주기 reconciliation 스케줄러가 없음. |

### 7.3 Signals / Factors (#71)

| 기능 | 상태 | 비고 |
|---|---|---|
| 팩터 레지스트리 전체 (`rsi`, `sma`, `sma_cross`, `atr`, `macd`, `bollinger`, `realized_vol`) | Partial | **레지스트리 조회는 가능** (`compute("rsi", close=...)`), **Strategy 의 `required_factors` 훅 배선은 dormant** (상단). MomoBtcV2 는 여전히 `from signals.rsi import compute_rsi` 직접 import. |
| `src/signals/cache.py` (Parquet Factor 캐시) | Dormant | `write_factor_parquet` / `read_factor_parquet` 구현되어 있으나 현재 호출하는 파이프라인 없음. 테스트 round-trip 만 동작. |
| `src/signals/lookahead_guard.py::assert_no_lookahead` | Test-only | `tests/signals/test_lookahead_guard.py` 에서만 호출. CI 레벨에서 자동 실행되지는 않음 (테스트가 invariants 와 별도). |

### 7.4 Universe (#87)

| 기능 | 상태 | 비고 |
|---|---|---|
| `StabilityGrade.grade()` | Pure function, no caller yet | DSL 배선 (`per_portfolio_risk.stability_grade_min`) 은 out-of-scope. 수집 어댑터 (CoinGecko API 등) 도 미구현. |

### 7.5 기타

| 기능 | 상태 | 비고 |
|---|---|---|
| `src/observability/` Prometheus 메트릭 (#26) | Partial | 메트릭 방출 함수는 있으나 대부분 코드 경로에서 호출되지 않음 (risk `evaluate()` 내부 `qta_risk_breach_total` 정도). Grafana 대시보드 JSON 은 있지만 실제 수집/송출 검증되지 않음. |
| `services/doc_agent/` 자동 초안 생성 (#53) | Active | `scripts/run_backtest.py` 가 백테스트 완료 후 호출. 인시던트/포스트모템 초안은 dormant (트리거 없음). |
| `services/obsidian_mcp/` (#51) | Active (external) | Claude Code 등 외부 LLM 이 도구로 사용. 프로젝트 런타임에서는 직접 호출 없음 (의도된 설계). |

### 7.6 활성화 여부 체크리스트 (라이브 운용 전)

- [ ] #78 (async 오케스트레이터) — `StrategyOrchestrator` 배선
- [ ] #79 (전략 카탈로그 확장) — 2+ 전략 → 오케스트레이터 의미 확보
- [ ] #80 (라이브 실행 프레임워크) — Execution 알고리즘 + KillSwitch 트리거 배선
- [ ] #81 (팩터 점증 계산) — `required_factors` 훅 프로덕션 사용 가능화
- [ ] #85 (메타라벨링) — `win_probability` 출력 슬롯 채움
- [ ] #86 (Whitepaper) — 섹션 11 "로드맵 & 진척도" 가 본 카탈로그 §7 를 흡수

---

## 8. 갱신 원칙

1. **새 opt-in 기능** 을 `src/` 에 추가한 모든 이슈는 머지 전 이 카탈로그에 행을 추가해야 한다.
2. 행은 다음을 포함: **활성화 방법** (YAML 키 또는 함수 호출), **기본값**, **파일 경로**, **테스트 파일**, **이슈 번호**.
3. 항목 삭제 시: "Deprecated" 섹션으로 이동 + 제거 예정 버전/이슈 명시.
4. **Dormant → Active 전환**: 배선 이슈 머지 시 §7 에서 해당 행을 §1~§6 로 이동 + "배선 방법" 컬럼 채움.
5. `docs/specs/risk-rule-dsl.md` §8.1 과 중복 항목은 이 문서에서 요약 + `§8.1` 링크.

## 9. 관련 노트

- `docs/specs/risk-rule-dsl.md` — DSL 필드 YAML 스키마 상세 (§8.1 #87 확장)
- `docs/onboarding/getting-started.md` — 일상 워크플로우
- `docs/background/31-uprich-patent-analysis.md`, `32-patents-portfolio-optimization.md` — #87 특허 회피 근거
- `docs/work/done/000087-patent-risk-refactor/01_plan.md` — #87 구현 계획
- 이슈 #69 (사이징), #70 (포트폴리오 리스크), #71 (팩터 레지스트리), #87 (특허 차용)

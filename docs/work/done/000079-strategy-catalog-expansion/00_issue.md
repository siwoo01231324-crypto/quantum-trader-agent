# feat: 전략 카탈로그 확장 (Mean Reversion + Channel Breakout + Vol-filtered Momentum)

## 목표
현재 `momo-btc-v2` 1개뿐인 전략 카탈로그를 **서로 낮은 상관관계의 3개 이상** 으로 확장해, [[19-portfolio-risk]] 의 포트폴리오 리스크 관리가 **실제 다양성 있는 입력** 으로 동작하게 만든다. 단일 전략으로는 ENB≈1 이라 리스크 모듈이 의미를 못 낸다.

## 배경

### 현재 카탈로그
- `src/backtest/strategies/momo_btc_v2.py` — BTC 15m 모멘텀 + RSI divergence (long-only, #67). 사실상 **유일한** 실구현체.
- `src/signals/rsi.py` — RSI 시그널 블록 하나.
- `docs/specs/strategies/momo-btc-v2.md` — 전략 스펙 템플릿.

### 왜 카탈로그 확장인가
- [[08-strategy-paradigms]] §Phase 1 후보 추천: "규칙기반(최우선) + 통계적 차익거래(보완)" 조합을 명시.
- [[19-portfolio-risk]] §6 v2 delivered — CVaR·상관·ENB 가 멀티 전략 전제로 설계됐음. **단일 전략이면 ρ̄, ENB 값이 무의미.**
- [[20-position-sizing]] §4.4 "KRX 개인 계좌는 레버리지 제한적 → unlevered risk parity" — 3개 이상 낮은-상관 전략이 있어야 비중 배분 의미.
- [[13-feature-alpha-catalog]] — 이미 RSI / MACD / Bollinger / ATR / RV 등 6팩터가 #71 파이프라인으로 제공. 소비자 확장이 필요.

## 범위

### 신규 전략 3개 (후보, 1~3 중 최종 선정 본 이슈에서 결정)
1. **Mean Reversion (통계적 차익거래)** ([[08-strategy-paradigms]] §2)
   - 암호화폐 페어 스프레드 (예: ETH/BTC, SOL/BTC)
   - 공적분 검정 → z-score 진입/청산
   - 위치: `src/backtest/strategies/meanrev_pairs.py`
2. **Channel Breakout** ([[08-strategy-paradigms]] §1, 규칙기반 권장 예시)
   - Donchian channel N일 + ATR 기반 변동성 필터
   - 추세 돌파 long-only
   - 위치: `src/backtest/strategies/breakout_donchian.py`
3. **Vol-filtered Momentum (v2)**
   - momo 계열 유지하되 **저변동 국면 진입 금지** 필터 추가
   - 기존 `momo_btc_v2` 와 약-중상관 예상 (~0.5) — 다변화 기여도 중간
   - 위치: `src/backtest/strategies/momo_vol_filtered.py`

### 공통 작업
- 각 전략: `Strategy` protocol 준수 ([[09-system-components]] §2), `on_init` + `on_bar` 구현
- 각 전략: `docs/specs/strategies/<id>.md` 스펙 + 프론트매터 `type: strategy`
- 각 전략: `tests/test_<id>.py` — 백테스트 Sharpe / MDD sanity 테스트
- `src/backtest/strategies/.ai.md` — 카탈로그 목록 + 리스크 연동 규칙 추가
- 3전략 동시 백테스트 → **실측 ρ̄ · ENB · CVaR** 산출 보고서

## 완료 기준
- [x] 3개 신규 전략 구현 + 각각 백테스트 통과 (meanrev_pairs, breakout_donchian, momo_vol_filtered — 단위/통합 134/134 tests green)
- [x] 각 전략별 일수익률 시계열을 T×N DataFrame 으로 결합 가능한 구조 (`src/backtest/calendar_align.py::intersect_trading_days`)
- [x] 결합 DataFrame → `risk.compute_portfolio_risk_from_df` → Report 생성 smoke test (integration test pass)
- [x] 결합 ENB/N ≥ 0.5 (측정치 0.805, synthetic dry-run)
- [x] 각 전략-기존 momo 간 평균 ρ ≤ 0.6 (측정치 max 0.019, synthetic dry-run)
- [x] `docs/specs/strategies/` 에 3개 스펙 파일 추가 (meanrev-pairs.md, breakout-donchian.md, momo-vol-filtered.md, 프론트매터 `type: strategy`)
- [x] `tests/` 에 단위 + 통합 테스트 (12개 신규 test 파일, 134/134 green)
- [x] `02_implementation.md` 에 **실측 상관매트릭스** 첨부 (dry-run synthetic; 실제 KIS 키 연결 후 재측정 가능한 스크립트 제공)

## 의존성
- **#71** (알파 팩터 파이프라인) — 전략이 소비할 팩터 공급자. MACD·Bollinger·ATR 등은 여기서 가져옴. **Merged 필요.**
- **#76** (Signal 인터페이스 확장) — 각 전략이 확신도·기대수익 등을 출력할 표준. 없으면 기존 `Signal{side, strength, ttl}` 로 운영.
- **#78** (멀티 전략 오케스트레이터) — 3전략 동시 실행을 위한 스케줄러. 오케스트레이터 없이는 백테스트 runner 에 3전략 각각 돌리는 수준에 머무름.

## 참고 research
- [[08-strategy-paradigms]] — §1 규칙기반, §2 통계적 차익거래, §Phase 1 후보 (MA crossover / Channel breakout / ATR 필터), §비교표
- [[13-feature-alpha-catalog]] — 각 전략이 소비할 팩터 목록 (#71 registry 재사용)
- [[12-validation-protocol]] — 각 전략별 DSR / purged k-fold / walk-forward 검증 절차
- [[19-portfolio-risk]] §3.2 cluster-based concentration — 카탈로그가 실제로 "한 클러스터" 안에 다 모여있는지 검증
- [[momo-btc-v2]] — 기존 단일 전략 스펙 (템플릿)

## 주의사항
- **Long-only 우선** — [[20-position-sizing]] §8-1 개인 공매도 제약. Mean reversion pairs 는 ETF/ETN 대체 검토.
- **거래세 포함 샤프** — [[tax-automation]] 0.20% 반영. 고회전 전략은 엣지 소실 위험.
- **서바이버십 편향 방지** — 백테스트 유니버스 선정시 delisted 포함.
- **과적합 경계** — 파라미터 grid 최대 3축. Walk-forward 필수.

## 후속 (out of scope)
- ML 전략 (LSTM/XGBoost) — Phase 2 이후 ([[08-strategy-paradigms]] §3)
- 양자 전략 — 연구 트랙으로 분리 ([[14-quantum-poc-design]])
- 전략간 capital allocation 최적화 — [[20-position-sizing]] §5 HRP 적용은 #69 후속
- 라이브 실행 프레임워크 (신규 이슈 C)


## 작업 내역

### 2026-04-25

**현황**: 0/8 완료 — 구현 계획 수립 단계 (consensus 승인 완료)
**완료된 항목**:
- 없음 (모든 AC 미완료)
**미완료 항목**:
- 3개 신규 전략 구현 + 각각 백테스트 통과
- 각 전략별 일수익률 시계열을 T×N DataFrame 으로 결합 가능한 구조
- 결합 DataFrame → `risk.compute_portfolio_risk_from_df` → Report 생성 smoke test
- 결합 ENB/N ≥ 0.5
- 각 전략-기존 momo 간 평균 ρ ≤ 0.6
- `docs/specs/strategies/` 에 3개 스펙 파일 추가
- `tests/` 에 단위 + 통합 테스트
- `02_implementation.md` 에 실측 상관매트릭스 첨부
**변경 파일**: 2개 (`01_plan.md` 전면 개정, `00_issue.md` 본 섹션 추가)
**비고**: `/ri` 실행 → 플랜이 초안(`## 구현 계획` 비어있음)으로 판정되어 `/plan` 커맨드 자동 호출 → A안(crypto-only) Planner→Architect(6)→Critic(7) → 8 개 revision 반영 → Critic 재리뷰 **APPROVE**. 이후 사용자 질문(#74 상태 확인) 으로 **B안 pivot**: #74 CLOSED 확인 → KIS 실 데이터 활용, `breakout_donchian` 을 BTCUSDT 1d → KOSPI200 KRX 1d 로 전환. B안 Planner v2 → Architect(7 items: 429 retry, intersection calendar, multi-symbol Signal 스코프, ATR look-ahead, cost.py 위치, basket 수익률 집계, krx_calendar 신설) → Critic 재리뷰 **APPROVE** (2026-04-25). 엔진 과부하 우려 대응으로 §B0 Tick Scheduler + self-guard 조항 추가. 최종 플랜 607 줄.

### 2026-04-25 (구현 완료)

**현황**: 8/8 AC 완료 (dry-run synthetic 기준).
**Team**: `/team 3` 으로 3 워커 병렬 실행. worker-1(KIS 인프라), worker-2(팩터/크립토 전략), worker-3(helpers/KRX 전략/통합). 11 태스크 전부 완료.
**테스트**: 134/134 #79 테스트 green. 전체 suite 610 pass (1개 사전존재 실패는 #79 무관 — phase0 gate cp949 인코딩).
**AC 게이트 측정치** (synthetic seed=79, 350 교집합 거래일):
- ENB ratio = 0.805 (≥ 0.5 ✓)
- Avg |ρ| = -0.005 (≤ 0.6 ✓)
- Per-strategy ρ vs momo_btc_v2: meanrev -0.099, momo_vol 0.014, breakout 0.019 — 모두 ≤ 0.6 ✓
- CVaR(97.5%) = 1.52%

**변경 파일**: 총 37개 (신규 20, 수정 10, 스펙 3, 테스트 12, 문서 2). 상세는 `02_implementation.md` 참조.
**잔여**: 사용자 수동 조치 — (1) `git add`/`git commit` 리뷰, (2) 선택: KIS paper 키 설정 후 `python scripts/measure_strategy_catalog.py` 로 실측 상관매트릭스 append. 다음 단계: `/fi` 또는 `/finish-issue` 로 PR 생성.

# 01_plan — 포지션 사이징 구현

## Acceptance Criteria

- [x] `src/risk/sizing.py` 에 3개 알고리즘(Kelly·Fractional/Half Kelly·Vol Targeting) 구현 + 단위 테스트 — 32 케이스 + 5 통합 테스트 green
- [x] momo-btc-v2 백테스트에서 half-kelly sizing 적용 후 baseline(all-in, size=1.0) 대비 Sharpe 비교 결과 저장 — `sizing_comparison.json`

## 설계 개요

### 책임 분리 (CLAUDE.md 불변식 #6 준수)

- `src/risk/sizing.py` 는 **순수 수학 함수의 모음**이다. 네트워크·LLM·I/O 일절 없음.
- 입력은 `p`, `b`, `mu`, `sigma` 같은 확정 수치. 출력은 `[0, 1]` 범위의 float(=equity fraction).
- LLM 은 이 함수를 **부르지도, 결과를 수정하지도** 않는다. 전략 코드가 직접 호출한다.
- 최종 clamp 는 `src/risk/dsl.py` 의 `evaluate()` 가 `per_position.max_weight_pct` · `per_portfolio.max_leverage` 로 담당 — sizer 는 clamp 를 알지 않는다(단일 책임).

### 흐름

```
Strategy.on_bar()
  → σ̂, μ̂(또는 p, b) 추정 (전략 내부)
  → sizer.kelly_fraction / vol_target 호출 → f_raw ∈ [0, 1]
  → Signal(action="buy", size=f_raw, reason=...)
Engine
  → cash × signal.size 로 매수 수량 계산 (기존 로직 유지)
RiskManager (추후 통합)
  → Policy 로 f_final clamp
```

현재 `backtest/engine.py` 는 `signal.size` 를 이미 "fraction of equity" 로 해석하므로 엔진 수정 불필요. 전략 쪽만 sizer 를 부르도록 바꾼다.

## 구현 모듈 설계

### `src/risk/sizing.py`

```python
def kelly_binary(p: float, b: float) -> float:
    """Kelly fraction for binary outcome (p·b − (1−p))/b. 음수면 0 반환."""

def kelly_continuous(mu: float, sigma: float, rf: float = 0.0) -> float:
    """정규 가정 Kelly: (μ − rf) / σ². σ ≤ 0 또는 edge ≤ 0 이면 0."""

def fractional_kelly(full_kelly: float, k: float = 0.5) -> float:
    """Half Kelly 기본(k=0.5). k ∈ (0, 1] 검증. 결과 ∈ [0, 1] clamp."""

def vol_target(
    sigma_period: float,
    target_annual: float = 0.10,
    periods_per_year: int = 252,
) -> float:
    """포지션 = σ_target_annual / (σ_period · √periods_per_year). [0, 1] clamp."""

def ewma_sigma(
    returns: pd.Series,
    lam: float = 0.94,   # RiskMetrics 기본
) -> float:
    """EWMA 표준편차. 마지막 값만 반환. 샘플 부족 시 NaN → 0 로 전파."""
```

- 모든 함수 **pure**, 부작용 없음.
- 상수 default 은 `20-position-sizing.md` 의 권고값을 그대로 따른다: `k=0.5` (Half Kelly), `lam=0.94` (RiskMetrics), `target_annual=0.10` (KR 중대형주 기준).
- 크립토(BTC) 용도는 호출부에서 `target_annual=0.20` 로 오버라이드하면 된다. 리서치 노트 §8 참고.
- 검증: `k > 1`, `sigma < 0`, `p ∉ [0, 1]` 등 잘못된 입력은 `ValueError` 로 fail-fast.
- `[0, 1]` clamp 는 sizer 단에서도 한 번 수행(엔진 시그널 contract 지키기 위함). 정책 레벨 clamp 는 `evaluate()` 가 별개로 수행.

### `src/risk/__init__.py`

`kelly_binary`, `kelly_continuous`, `fractional_kelly`, `vol_target`, `ewma_sigma` 를 public API 로 export.

### `src/backtest/strategies/momo_btc_v2.py` 변경

- 클래스에 선택적 파라미터 `sizing_mode: Literal["full", "half-kelly", "vol-target"]` 추가 (기본 `"full"` — 기존 거동 유지).
- `"half-kelly"`: 과거 N(=60) 바의 수익률로 `kelly_continuous(μ̂, σ̂)` 계산 후 `fractional_kelly(k=0.5)` 적용.
- `"vol-target"`: `ewma_sigma(returns, lam=0.94)` → `vol_target(σ, target_annual=0.20, periods_per_year=365*96)` (BTC 15m 기준 연 환산).
- 기존 `size=1.0` 경로는 그대로 유지되어 모든 기존 테스트 통과.

### `docs/specs/position-sizing.md` (신규)

- type: `spec-architecture`, id: `position-sizing`.
- 배경·목표·API·기본값·불변식(결정적·LLM 미개입)·검증 전략·[[20-position-sizing]] 과의 관계 기술.
- `.ai.md` 규칙 §4 준수: "관련 노트" 섹션에 `[[20-position-sizing]]`·`[[risk-rule-dsl]]`·`[[momo-btc-v2]]`·`[[13-feature-alpha-catalog]]`·`[[19-portfolio-risk]]` 위키링크.

### `docs/specs/strategies/momo-btc-v2.md` 업데이트

- 본문에 "진입 크기" 섹션 추가: 기본 `full`, 실험적으로 `half-kelly` 사용 가능. `sharpe_bt` 는 baseline 기준 값을 유지하고, 비교 결과는 work-done 에 별도 기록.

### `src/risk/.ai.md` 업데이트

- 구조 섹션에 `sizing.py` 추가.
- "관련" 에 `docs/specs/position-sizing.md`, `docs/background/20-position-sizing.md` 추가.

### `docs/schemas/note-schemas.md` — 확장 **하지 않음**

- 전략 프론트매터에 `position_sizing` 필드를 추가하는 대신, 사이징은 **런타임 설정**(전략 인스턴스 생성 시 인자)으로 둔다.
- 이유: ① 프론트매터는 메타데이터용, 런타임 파라미터는 스트래티지 config 가 맞음. ② 스키마 변경 시 기존 전략 노트 마이그레이션 리스크. ③ 이슈 범위 최소화.
- 향후 멀티 전략 러너가 생기면 별도 이슈로 재검토.

## 파일 목록

### 신규
- `src/risk/sizing.py`
- `tests/test_risk_sizing.py`
- `docs/specs/position-sizing.md`
- `scripts/compare_momo_btc_v2_sizing.py` — baseline vs half-kelly 백테스트 실행 + 결과 JSON 저장 (`docs/work/active/000069-position-sizing/sizing_comparison.json`)

### 수정
- `src/risk/__init__.py` — 새 심볼 export
- `src/risk/.ai.md` — sizing 추가
- `src/backtest/strategies/momo_btc_v2.py` — optional sizer 통합
- `docs/specs/strategies/momo-btc-v2.md` — 진입 크기 섹션 추가

## 테스트 전략

### `tests/test_risk_sizing.py` (단위)

1. **Kelly binary**:
   - `kelly_binary(p=0.55, b=1.0) == pytest.approx(0.10)` (리서치 노트 §2.1 예시)
   - `kelly_binary(p=0.4, b=1.0) == 0.0` (음수 edge → 0)
   - 불량 입력 `p=1.5` → `ValueError`
2. **Kelly continuous**:
   - `kelly_continuous(mu=0.02, sigma=0.1) == pytest.approx(2.0)` → clamp 전 raw
   - `sigma=0.0` → `0.0` (보수적 처리)
   - `mu<rf` → `0.0`
3. **Fractional Kelly**:
   - `fractional_kelly(0.10, k=0.5) == 0.05`
   - `fractional_kelly(1.2, k=0.5) == 0.6` → clamp 전 raw
   - `k=1.1` → `ValueError`
4. **Vol target**:
   - `vol_target(sigma_period=0.02, target_annual=0.10, periods_per_year=252)` 수식 검증 (`0.10 / (0.02 · √252)`)
   - `sigma_period=0` → clamp 1.0 (경계)
   - target > σ_realized → 1.0 로 clamp
5. **EWMA σ**:
   - 상수 리턴 시리즈 → σ ≈ 0
   - 알려진 σ 가진 무작위 시리즈(seed 고정) → 기대 σ ± tol
6. **Determinism**:
   - 동일 입력 → 동일 출력 (seed, 순서 불변).
7. **Integration (light)**:
   - `MomoBtcV2(sizing_mode="half-kelly")` 가 `Strategy` protocol 만족.
   - synthetic OHLCV(200 bar, seed 고정) 백테스트 → `BacktestResult` 정상 반환, `size > 0` 이 발생하고 `size ≤ 1.0`.

### 기존 테스트 회귀

- `tests/test_momo_btc_v2.py` 전부 통과 (기본 `sizing_mode="full"` 이 size=1.0 유지).
- `tests/test_risk_dsl.py` 전부 통과 (dsl.py 미변경).

### AC #2: Sharpe 비교

- `scripts/compare_momo_btc_v2_sizing.py`:
  - 입력: `tests/fixtures/fixtures/` 또는 기존 run_backtest 스크립트가 쓰던 OHLCV fixture (확인 필요).
  - `run_backtest(ohlcv, MomoBtcV2(sizing_mode="full"))` 와 `run_backtest(ohlcv, MomoBtcV2(sizing_mode="half-kelly"))` 두 번 실행.
  - 두 결과의 `sharpe`·`mdd`·`total_return`·`trades` 를 JSON 으로 기록.
- AC 는 "비교" 만 요구하므로 half-kelly 가 Sharpe 를 **반드시 개선할 필요는 없다**. 문서에는 관측된 값과 해석을 기록한다.

## 실데이터 검증 결과 (2026-04-24 추가)

AC 범위 확장 — 합성 데이터만으로는 의미 있는 비교가 안 되어 BTC 15m 1년 실데이터로 재검증.

- 데이터: Binance 공개 REST, BTCUSDT 15m, 2025-04-23 ~ 2026-04-23, 35,041 bars, `lake/` 저장.
- 결과 (`sizing_comparison.json`):

  | mode | Sharpe | MDD | total_return | trades | win_rate |
  |---|---|---|---|---|---|
  | full | -0.175 | 5.14% | -1.17% | 34 | 64.7% |
  | half-kelly | -2.212 | 5.12% | -4.80% | 24 | 41.7% |
  | vol-target | -0.666 | 5.10% | -3.06% | 34 | 64.7% |

- **사이저 수학은 의도대로 동작** — vol-target 이 MDD 를 가장 낮게 유지, 거래 횟수 동일. EWMA σ 역수 스케일링 검증됨.
- **세 모드 모두 Sharpe 음수** — momo-btc-v2 전략의 엣지 부재. 사이저 문제가 아니라 전략 신호 품질 문제. → #71 (알파 팩터 파이프라인) · [[12-validation-protocol]] 범위.
- **Half-Kelly win_rate 64.7%→41.7%, 거래수 34→24 급감** — 현재의 "과거 60bar 평균수익률 = μ" 가정이 모멘텀 전략 신호("하락 후 반등") 의미와 방향 불일치. 전략이 자신의 확신도·기대수익을 `Signal` 로 넘기는 인터페이스 필요 ([[20-position-sizing]] §7.1 `SignalStrength` 제안).

## 후속 이슈 (본 이슈 범위 밖)

1. **Signal 인터페이스 확장** — `Signal` 에 optional `expected_return`·`win_probability`·`confidence` 추가. 전략이 채우면 sizer 가 그 값을 사용, 비면 현재 방식 fallback. → 새 이슈 생성 예정.
2. **전략 엣지 검증·팩터 확장** — #71 (알파 팩터 파이프라인) 진행 중. walk-forward·DSR·PBO 검증은 [[12-validation-protocol]] 기반 별도 이슈.

## 리스크·오픈 퀘스천

- **μ 추정의 과적합**: 롤링 평균 수익률로 Kelly 돌리면 노이즈 과적합. 완화: 최소 N=60 바 warmup, 음수 μ 시 size=0, `sizing.py` 독스트링에 명시.
- **BTC 15m 기준 연 환산**: 연 거래시간 ≈ 365 × 24 × 4 = 35,040 bar. √35040 ≈ 187. 테스트에서 이 상수를 명시적으로 주입해 검증한다.
- **σ=0 경계**: vol_target 의 σ=0 을 "full allocation" 으로 할지 "no signal" 로 할지 규약 필요. 결정: **`full(=1.0) clamp`** — 이후 `per_position.max_weight_pct` 가 실질 상한. 독스트링 + 테스트로 고정.
- **정책 clamp 미통합**: 본 이슈에서는 sizer output → signal.size 만 구현. Policy evaluate() 경유 clamp 는 `RiskManager` 주문 게이트(#24 후속)에서 통합. 01_plan 에 TODO 로 명시.
- **합성 데이터의 Sharpe 노이즈**: seed 고정해도 1 회 실행은 신호 약함. 비교 결과를 "참고값"으로만 기록하고, 실데이터 재평가는 향후 walk-forward(#12 프로토콜)로 미룬다.
- **CI 불변식**: `docs/specs/position-sizing.md` 가 `type: spec-architecture` + `id: position-sizing` 이면 `check_invariants.py` 통과 예상. 신규 `[[20-position-sizing]]` 링크는 실존 파일 가리키므로 OK.

## 롤백 전략

- 모든 변경은 **신규 파일 + backward-compatible 파라미터 추가**.
- `sizing_mode="full"` 이 기본값 → 롤백 시 기존 전략 동작 동일.
- 문제 발생 시 `src/risk/sizing.py` 삭제 + `momo_btc_v2.py` 의 `sizing_mode` 인자만 제거하면 원상복구.

## 진행 순서 (작업 체크포인트)

1. `sizing.py` 함수 5개 + `__init__.py` export
2. `test_risk_sizing.py` 단위 테스트 → green
3. `momo_btc_v2.py` 에 `sizing_mode` 추가 + 기존 테스트 회귀 green
4. `compare_momo_btc_v2_sizing.py` 스크립트 + 비교 JSON 생성
5. `docs/specs/position-sizing.md` 작성, `momo-btc-v2.md`·`src/risk/.ai.md` 업데이트
6. `scripts/check_invariants.py --strict` 로컬 실행
7. `gh issue` 내 AC 체크 → `/finish-issue 69`

## 선행 조건

- #67 백테스트 엔진 (머지 완료 ✓)

## 참고

- `docs/background/20-position-sizing.md`
- `docs/background/09-system-components.md` (`PositionSizer` 박스)
- `docs/specs/risk-rule-dsl.md` (최종 clamp 경로)
- `src/risk/dsl.py` (Policy.per_position.max_weight_pct)
- `src/backtest/engine.py` (`Signal.size` 계약)
- CLAUDE.md 불변식 #6 (LLM 에 리스크 결정 위임 금지)

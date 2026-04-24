---
type: spec-architecture
id: position-sizing
name: "Position Sizing (Issue #69)"
owner: siwoo
status: draft
tags: []
---

# Position Sizing (Issue #69)

## 1. 목적

전략이 **방향**(매수/매도)을 결정한 이후 **크기**(계좌의 몇 %를 쓸지) 를 계산하는 단계를 별도 모듈 `src/risk/sizing.py` 로 분리한다. 현재 백테스트 엔진은 `Signal.size` 를 equity fraction (0–1) 으로 해석하므로, 전략이 기존에 `size=1.0` (올인) 으로 반환하던 경로를 **수학적으로 정당화된 비율**로 대체하는 것이 본 모듈의 역할이다.

이론적 근거는 [[20-position-sizing]] 에 정리되어 있다. 최종 정책 clamp 는 [[risk-rule-dsl]] 의 `per_position.max_weight_pct` · `per_portfolio.max_leverage` 가 담당한다 — 본 모듈은 raw size 만 계산하고 정책 clamp 를 **알지 않는다**.

## 2. 설계 원칙

1. **결정적(deterministic)**: 동일 입력 → 동일 출력. 난수·시간·외부 호출 없음. (CLAUDE.md 불변식 #6 — LLM 에 리스크 결정 위임 금지)
2. **순수 함수**: 입력은 확정 수치(`p`, `b`, `mu`, `sigma`, `returns`), 출력은 `float ∈ [0, 1]`.
3. **fail-closed**: 불확실하거나 엣지가 없는 상황(`sigma=0` 인 Kelly, `edge ≤ 0`) 에서는 `0.0` 을 반환. 포지션을 진입하지 않는 쪽이 기본.
4. **단일 책임**: 정책 clamp 는 본 모듈의 역할이 아니다. `[0, 1]` clamp 만 여기서, `max_weight_pct` clamp 는 `risk.dsl.evaluate` 에서.
5. **버전드 기본값**: 기본 Kelly 계수 `k=0.5`, EWMA `λ=0.94`, vol target `10%` (KR) / `20%` (crypto) — 모두 [[20-position-sizing]] 에서 근거.

## 3. API

```python
from risk.sizing import (
    kelly_binary,         # (p, b) -> [0, 1]
    kelly_continuous,     # (mu, sigma, rf=0.0) -> [0, 1]
    fractional_kelly,     # (full, k=0.5) -> [0, 1]
    vol_target,           # (sigma_period, target_annual=0.10, periods_per_year=252) -> [0, 1]
    ewma_sigma,           # (returns, lam=0.94) -> float
)
```

시그니처·검증·예외는 독스트링 단일 출처. 잘못된 입력(`p ∉ [0, 1]`, `k ∉ (0, 1]`, `sigma < 0`, `lam ∉ (0, 1)`) 은 `ValueError` 로 **즉시 실패**.

### 3.1 합성 패턴

표준 합성은 "Kelly raw → fractional" 순이다.

```python
full  = kelly_continuous(mu=sample_mean, sigma=sample_std)
size  = fractional_kelly(full, k=0.5)     # Half Kelly
# 또는
size  = vol_target(sample_std, target_annual=0.20, periods_per_year=365*96)
```

## 4. 기본값과 근거

| 기본값 | 값 | 근거 |
|---|---|---|
| Kelly k | 0.5 (Half Kelly) | Thorp 1997, [[20-position-sizing]] §2.4 |
| EWMA λ | 0.94 | RiskMetrics 1996 일간 표준 |
| Vol target (KR 주식) | 10% 연율 | KOSPI 연 18% 대비 보수적, [[20-position-sizing]] §3.3 |
| Vol target (crypto) | 20% 연율 | BTC 연 60%+ 대비 보수적, 호출부에서 override |
| periods_per_year (BTC 15m) | 35,040 | 365 × 96 bars |
| 최소 lookback | 2 | 분산 계산 요건; 전략 레벨은 60 권장 |

## 5. 전략 통합 예시 — momo-btc-v2

`MomoBtcV2(sizing_mode=…)` 에 3 가지 모드 지원:

- `"full"` (default, 하위호환): 기존 `size=1.0` 유지.
- `"half-kelly"`: 최근 `sizing_lookback`(기본 60) bar 의 수익률로 `mu`·`sigma` 추정 → `kelly_continuous` → `fractional_kelly(k=0.5)`.
- `"vol-target"`: `ewma_sigma(returns, λ=0.94)` → `vol_target(target_annual=0.20, periods_per_year=35040)`.

매도 시그널은 항상 `size=1.0` (전량 청산) 으로 유지한다 — 사이징은 **진입 크기**에 대한 결정이지 청산 타이밍에 대한 결정이 아니다.

## 6. AC 검증

- `tests/test_risk_sizing.py` (32 케이스 + 5 통합): 수식 레퍼런스·경계값·입력 검증·pandas/numpy 호환·결정성·전략 통합.
- `scripts/compare_momo_btc_v2_sizing.py`: 동일 OHLCV 로 `full` · `half-kelly` · `vol-target` 3 백테스트 후 Sharpe·MDD·total_return·trades 을 JSON 으로 저장. 결과는 `docs/work/active/000069-position-sizing/sizing_comparison.json`.

### 6.1 실데이터 검증 (BTC 15m, 2025-04-23 ~ 2026-04-23, 35,041 bars)

| mode | Sharpe | MDD | total_return | trades | win_rate | final_equity |
|---|---|---|---|---|---|---|
| full (baseline) | -0.175 | 5.144% | -1.171% | 34 | 64.7% | 9,883 |
| half-kelly | -2.212 | 5.117% | -4.799% | 24 | 41.7% | 9,520 |
| vol-target | -0.666 | 5.097% | -3.062% | 34 | 64.7% | 9,694 |

**관찰**:

1. **사이저는 의도대로 동작한다** — vol-target 이 MDD 를 가장 낮게 만들고 거래 횟수는 baseline 과 동일. EWMA σ 스케일링이 수학대로 적용되고 있다는 증거.
2. **전략 자체에 엣지가 없다** — 세 모드 모두 Sharpe 음수. [[momo-btc-v2]] 의 RSI divergence 신호가 BTC 1년 데이터에서 수익 엣지를 만들지 못함. 이 부분은 [[12-validation-protocol]] walk-forward 검증 및 #71 (알파 팩터 파이프라인) 의 범위.
3. **Half-Kelly μ 추정 실패** — 거래 수 34→24, 승률 64.7%→41.7% 로 급락. 원인: 현재 구현은 "최근 60bar 평균수익률" 을 μ 로 쓰는데, momo 전략 특성상 "최근 하락 후 반등" 이 진짜 매수 타이밍이므로 전략 신호와 μ 추정이 **방향이 어긋남**. [[20-position-sizing]] §7.1 이 이미 제안한 `SignalStrength(p, expected_return, sigma)` 인터페이스 — 전략이 **자기 확신도·기대수익**을 직접 넘겨주도록 `Signal` 을 확장하는 후속 이슈가 필요.

## 7. 불변식

## 7. 불변식

1. `sizing.py` 는 어떠한 LLM·네트워크·파일 I/O 도 수행하지 않는다.
2. 모든 사이저 함수의 반환값은 `[0, 1]` 을 벗어나지 않는다.
3. `sigma < 0`, `p ∉ [0, 1]`, `k ∉ (0, 1]`, `lam ∉ (0, 1)` 은 즉시 `ValueError`.
4. 전략 프론트매터에 `position_sizing` 필드를 추가하지 않는다 — 사이징은 **런타임 파라미터**로 처리한다.

## 8. 향후 작업 (실데이터 검증 결과 반영)

1. **`Signal` 인터페이스 확장 — Half-Kelly μ 추정 문제 해소** (#69 실데이터 결과 §6.1-3 참조)
   - 현재 `Signal(action, size, reason)` → `expected_return`, `win_probability`, `confidence` optional 필드 추가
   - 전략이 자기 확신도·기대수익을 채우면 사이저가 그 값을 사용, 비면 fallback (현재 방식)
   - 근거: [[20-position-sizing]] §7.1 `SignalStrength(p, expected_return, sigma)` 인터페이스 제안 — 실데이터에서 "전략의 판단" 과 "사이저의 μ 추정" 방향 불일치로 win_rate 64.7%→41.7% 급락을 확인
   - **delivered in #76** — [[signal-interface]] 참조
2. **`risk.dsl.evaluate` 와의 end-to-end 통합**: `OrderIntent` 단계에서 sizer output 을 `per_position.max_weight_pct` 로 clamp (별도 이슈).
3. **멀티 종목 사이징**: ERC · HRP ([[20-position-sizing]] §4·§5) — 20 종목 이상 포트폴리오가 생기는 시점에 별도 이슈.
4. **walk-forward 검증·전략 엣지 탐색**: 별도이나 #71 (알파 팩터 파이프라인) · [[12-validation-protocol]] 의 범위.

## 관련 노트

- [[20-position-sizing]] — 이론적 근거
- [[19-portfolio-risk]] — 공분산·포트폴리오 리스크 (ERC/HRP 입력)
- [[risk-rule-dsl]] — 최종 정책 clamp 경로
- [[momo-btc-v2]] — 본 모듈을 소비하는 첫 전략
- [[13-feature-alpha-catalog]] — ATR·EWMA σ 계산 근거
- [[09-system-components]] — `PositionSizer` 박스
- [[12-validation-protocol]] — 사이징 효과의 walk-forward 검증 (향후)

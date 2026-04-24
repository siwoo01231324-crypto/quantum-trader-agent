---
id: reliability-score
type: spec-architecture
name: Strategy Reliability Score 명세 — 곱셈 게이트 공식
owner: siwoo
status: active
---

# Strategy Reliability Score 명세

관련 노트: [[signal-interface]], [[factor-registry]], [[32-patents-portfolio-optimization]], [[20-position-sizing]], [[momo-btc-v2]]

---

## 1. 목적

전략의 누적 성과 통계를 단일 스칼라 [0, 1] 로 압축해 사이저·오케스트레이터가 포지션 가중을 조정할 수 있게 한다.

구현체: `src/portfolio/orchestrator.py::StrategyOrchestrator.strategy_reliability_score(strategy_id)`

---

## 2. 공식

```
reliability = convex_base × drawdown_gate
```

### 2.1 convex_base

```
convex_base = 0.4 × h(T) + 0.4 × Φ(t_IR) + 0.2 × (1 − CVaR_breach_rate)
```

- `h(T)` = `min(T / 252, 1.0)` — 이력 충분도 (1년 = 1.0)
- `Φ(t_IR)` = 정규 CDF(IR t-통계량) — 통계적 유의성
- `CVaR_breach_rate` = 일간 손실이 95% CVaR 임계값을 초과한 비율

### 2.2 drawdown_gate

```
drawdown_gate = clip(1.0 − max_dd_pct / 0.20, 0.0, 1.0)
```

- `max_dd_pct` = 전략의 최대 낙폭 (소수점, e.g. 0.10 = 10%)
- `max_dd_pct ≥ 0.20` → `drawdown_gate = 0.0` → `reliability = 0.0` (하드 제로)
- `max_dd_pct = 0.00` → `drawdown_gate = 1.0`

### 2.3 NaN 가드 (사전 조건)

```python
if T < 20:
    return 0.0  # avoids Φ(NaN) when t_IR is undefined for tiny samples
```

T < 20 이면 t_IR 이 정의되지 않아 NaN 이 전파된다. 첫 실행 줄에서 0.0 을 즉시 반환한다.

---

## 3. 작동 예시 (9 셀)

| T | max_dd_pct | convex_base (예시) | drawdown_gate | reliability |
|---|---|---|---|---|
| 20 | 5% | 0.50 | 0.75 | 0.375 |
| 20 | 10% | 0.50 | 0.50 | 0.250 |
| 20 | 20% | 0.50 | 0.00 | **0.000** |
| 126 | 5% | 0.72 | 0.75 | 0.540 |
| 126 | 10% | 0.72 | 0.50 | 0.360 |
| 126 | 20% | 0.72 | 0.00 | **0.000** |
| 250 | 5% | 0.85 | 0.75 | 0.638 |
| 250 | 10% | 0.85 | 0.50 | 0.425 |
| 250 | 20% | 0.85 | 0.00 | **0.000** |

`max_dd_pct = 0.20` (20%) 에서 항상 reliability = 0 이다 — convex_base 값에 무관하게.

---

## 4. 구현 시그니처

```python
def register_strategy_returns(
    strategy_id: str,
    returns: pd.Series,
    t_stat_ir: float,
    cvar_breach_rate: float,
    max_dd_pct: float,
    T: int,
) -> None: ...

def strategy_reliability_score(strategy_id: str) -> float: ...
```

`strategy_id` 는 Signal 이 아니라 이 메서드 호출 시 전달된다 (Signal 에서 제거된 이유: [[signal-interface]] §2).

---

## 5. 단조성·경계

- `reliability ∈ [0, 1]` (bounded)
- `drawdown_gate` 에 대해 단조 감소
- `max_dd_pct = 0.20` 에서 불연속 (하드 제로)

---

## 6. 관련 특허 구분

`KR101139626B1` (한국 특허 — 전략 신뢰도 기반 자산 배분) — **active 특허**.

**differs:** KR 청구항은 신뢰도 구성요소를 **가산 가중 합**으로 결합한다. 본 구현은 `convex_base × drawdown_gate` — **곱셈 게이트** 방식을 사용한다. 구조적 차이: `max_dd_pct ≥ 20%` 에서 drawdown_gate = 0 이므로 reliability 가 반드시 0 이 된다. 이 **불연속 지시 함수** (hard-zero at DD ≥ 20%)는 어떤 가산 또는 로그-가산 분해로도 표현 불가능하다 — `log(reliability) = log(convex_base) + log(gate)` 형태로 쓰더라도 floor-truncation `max(gate, ε)` 없이는 zero-output 보장이 오염된다. 따라서 청구항 범위에 해당하지 않는 독립적 구조다.

---
id: signal-interface
type: spec-architecture
name: Signal 인터페이스 명세 — 6-field 확장 + Signal-wins 우선순위
owner: siwoo
status: active
---

# Signal 인터페이스 명세

관련 노트: [[20-position-sizing]], [[factor-registry]], [[reliability-score]], [[rsi-divergence]], [[momo-btc-v2]]

---

## 1. 목적

전략이 "이 신호의 기대수익·확신도·승률"을 `PositionSizer` 로 직접 넘길 수 있도록 `Signal` 을 3→6 필드로 확장한다.

배경: `src/risk/sizing.py` 의 Half-Kelly 에서 μ 를 "최근 60bar 평균수익률"로 추정하면, 모멘텀 전략의 신호 방향과 불일치해 win_rate 64.7%→41.7% 로 급락하는 문제가 관찰됨.
[[20-position-sizing]] §7.1 의 `SignalStrength(p, expected_return, sigma)` 제안을 실장한다.

---

## 2. Signal 데이터클래스 (6 필드)

```python
@dataclass
class Signal:
    # --- 기존 3 필드 (변경 없음) ---
    action: str              # "buy" | "sell" | "hold"
    size: float              # fraction of equity (0.0 – 1.0)
    reason: str

    # --- 신규 3 필드 (kw-only Optional, 기본 None) ---
    expected_return: Optional[float] = None   # 전략이 추정한 기대수익 (소수점, e.g. 0.02 = +2%)
    win_probability: Optional[float] = None   # 전략이 추정한 승률 (0.0 – 1.0)
    confidence: Optional[float] = None        # 복합 확신도 (0.0 – 1.0)
```

### 제약

- `strategy_id` 는 Signal 에 없음. 전략 식별은 `register_strategy_returns(strategy_id, ...)` 호출 시점에서만 수행한다.
- `expected_return`, `win_probability`, `confidence` 값은 **결정적 코드 계산 결과만 허용**. LLM 출력을 이 필드에 직접 할당 금지 (CLAUDE.md 불변식 #6, [[34-patents-execution-algos]] 참조).

---

## 3. Signal-wins 우선순위 (sizer 통합)

사이저는 Signal 필드가 `None` 이 아닐 때 해당 값을 즉시 사용한다.

```python
er = signal.expected_return if signal.expected_return is not None else rolling_er_60b(...)
wp = signal.win_probability if signal.win_probability is not None else rolling_wp_60b(...)
```

### Zero vs None 의미론

| 값 | 의미 | sizer 동작 |
|---|---|---|
| `None` | 전략이 추정값을 제공하지 않음 | 60-bar rolling fallback 사용 |
| `0.0` | 전략이 명시적으로 기대수익=0 을 계산함 | `0.0` 을 그대로 사용 (fallback 하지 않음) |
| 양수/음수 float | 전략의 확신있는 추정 | 값을 그대로 사용 |

**Zero 는 None 이 아니다.** `Signal(expected_return=0.0)` → er = 0.0 (fallback 없음).

---

## 4. Kelly 경로 분기

`win_probability` 가 None 이 아닐 때 `kelly_binary` 경로가 활성화된다:

```
kelly_fraction = wp - (1 - wp) / (er / loss_estimate)
```

`win_probability = None` 이면 rolling 승률 추정을 사용한다.

---

## 5. 불변식 #6 — LLM 실행 경로 금지

이 필드들은 결정적 계산의 결과여야 한다. 허용 출처:

- 기술적 지표 수식 (RSI divergence 강도, ATR 비율 등)
- 과거 통계 (rolling win_rate, rolling mean_return)
- 기타 결정적 수학 함수

금지 출처:

- LLM API 호출 (`anthropic`, `openai`, `langchain` 등) 결과의 직접 할당
- 확률적 샘플링 (미 시드된 RNG 등)

`scripts/check_invariants.py::_check_llm_delegation` 가 `src/` 내 LLM import 를 정적으로 검출한다.

---

## 6. 하위 호환성

기존 3-arg 생성 경로는 그대로 유효하다:

```python
Signal(action="buy", size=0.1, reason="rsi_crossover")
# expected_return=None, win_probability=None, confidence=None
```

CI 의 `tests/test_signal_backcompat.py` 가 이를 회귀 방지한다.

---

## 7. 관련 특허 구분

`US20140081889A1` (Axioma — factor-exposure 기반 포지션 구성) — **abandoned**. 본 인터페이스는 Signal 의 메타데이터 전달 계층으로, Axioma 의 최적화 엔진 청구항과 구조적으로 다르다. 채택한 아이디어(signal 메타데이터 전달)는 청구항 범위에 포함되지 않으며, 해당 특허는 abandoned 상태다.

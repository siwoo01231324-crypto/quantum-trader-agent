---
id: factor-registry
type: spec-architecture
name: Factor Registry 명세 — FactorSpec 확장 + bar_interval 폐쇄 어휘
owner: siwoo
status: active
---

# Factor Registry 명세

관련 노트: [[signal-interface]], [[13-feature-alpha-catalog]], [[33-patents-factor-models]], [[momo-btc-v2]]

---

## 1. 목적

`src/signals/registry.py` 의 `FactorSpec` 에 `alpha_horizon_bars`, `bar_interval`, `signal_type` 을 추가해 각 팩터의 알파 지평선·타임프레임·신호 유형을 명시적으로 선언하게 한다.

---

## 2. FactorSpec 확장 필드

```python
@dataclass
class FactorSpec:
    name: str
    func: Callable[..., Any]
    inputs: list[str]
    default_params: dict[str, Any] = field(default_factory=dict)

    # --- 신규 필드 ---
    alpha_horizon_bars: int = 1
    bar_interval: str = "1d"
    signal_type: Literal[
        "momentum", "mean_reversion", "event", "value", "vol", "unknown"
    ] = "momentum"
```

---

## 3. bar_interval 폐쇄 어휘

`bar_interval` 은 아래 8개 값만 허용된다. 등록 시점(`@register()` 데코레이터)에 `ValueError` 를 발생시킨다.

| 값 | 초 환산 |
|---|---|
| `"1m"` | 60 |
| `"5m"` | 300 |
| `"15m"` | 900 |
| `"30m"` | 1800 |
| `"1h"` | 3600 |
| `"4h"` | 14400 |
| `"1d"` | 86400 |
| `"1w"` | 604800 |

알 수 없는 `bar_interval` (예: `"2h"`, `"3d"`) 은 **등록 시점**에 즉시 `ValueError` 를 발생시킨다 (런타임 지연 거부 아님).

---

## 4. signal_type 폐쇄 어휘

```
Literal["momentum", "mean_reversion", "event", "value", "vol", "unknown"]
```

알 수 없는 값은 Pydantic 또는 dataclass 검증자가 등록 시점에 거부한다.

---

## 5. 등록 예시 (7 표준 팩터)

| 팩터 | alpha_horizon_bars | bar_interval | signal_type |
|---|---|---|---|
| `rsi` | 5 | `"1d"` | `"mean_reversion"` |
| `sma` | 10 | `"1d"` | `"trend"` |
| `sma_cross` | 10 | `"1d"` | `"trend"` |
| `atr` | 1 | `"1d"` | `"volatility"` |
| `macd` | 10 | `"1d"` | `"momentum"` |
| `bollinger` | 5 | `"1d"` | `"mean_reversion"` |
| `realized_vol` | 20 | `"1d"` | `"volatility"` |

---

## 6. @register 데코레이터 시그니처

```python
def register(
    name: str,
    *,
    inputs: list[str],
    alpha_horizon_bars: int = 1,
    bar_interval: str = "1d",
    signal_type: str = "momentum",
    **defaults: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    ...
```

`bar_interval` 과 `signal_type` 이 폐쇄 어휘를 벗어나면 `ValueError` 를 즉시 발생시킨다.

---

## 7. 관련 특허 구분

`US8433645B1` (Portware — 알파 신호 기반 실행 최적화 시스템) — **active 특허**.

**differs:** 본 구현은 `bar_interval` 과 `alpha_horizon_bars` 를 `FactorSpec` 메타데이터로 **외부화**한다. Portware 의 청구항은 이 타이밍 정보를 실행 엔진 내부에 임베드한다. 우리는 팩터 등록 시 명시적 메타데이터로 선언하므로 청구항 범위와 구조적으로 다르다. 또한 본 구현은 실행 최적화 목적이 아닌 알파 지평선 감사·재현성 목적이다.

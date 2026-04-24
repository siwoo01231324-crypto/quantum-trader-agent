"""Factor registry: name -> callable mapping with signature-validated dispatch.

Patent reference: US8433645B1 (Portware — alpha-signal-based execution optimization) — active.
US8433645B1 differs: we externalize bar_interval and alpha_horizon_bars as FactorSpec metadata
declared at registration time. Portware embeds timing in the execution engine internals.
Our use is for alpha-horizon auditing and reproducibility, not execution optimization.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


DEFAULT_FACTOR_SET: str = "v1"

_VALID_BAR_INTERVALS: frozenset[str] = frozenset(
    {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
)

_VALID_SIGNAL_TYPES: frozenset[str] = frozenset(
    {"momentum", "mean_reversion", "volatility", "trend", "breakout", "event", "value", "vol", "unknown"}
)


@dataclass
class FactorSpec:
    name: str
    func: Callable[..., Any]
    inputs: list[str]
    default_params: dict[str, Any] = field(default_factory=dict)
    # Explicit alpha-horizon metadata (US8433645B1 differs: externalized, not engine-embedded)
    alpha_horizon_bars: int = 1
    bar_interval: str = "1d"
    signal_type: str = "momentum"


FACTOR_REGISTRY: dict[str, FactorSpec] = {}


def register(
    name: str,
    *,
    inputs: list[str],
    alpha_horizon_bars: int = 1,
    bar_interval: str = "1d",
    signal_type: str = "momentum",
    **defaults: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: @register("rsi", inputs=["close"], period=14).

    Validates at registration time that every entry in `inputs` appears in the
    decorated function's signature. Extra parameters (hyperparameters like
    `window`) are allowed but not required to match `inputs`.

    bar_interval must be one of: {"1m","5m","15m","30m","1h","4h","1d","1w"}.
    Unknown values raise ValueError immediately at decoration time.

    signal_type must be one of: {"momentum","mean_reversion","volatility","trend",
    "breakout","event","value","vol","unknown"}.
    """
    if bar_interval not in _VALID_BAR_INTERVALS:
        raise ValueError(
            f"factor {name!r}: bar_interval={bar_interval!r} not in closed vocabulary "
            f"{sorted(_VALID_BAR_INTERVALS)}"
        )
    if signal_type not in _VALID_SIGNAL_TYPES:
        raise ValueError(
            f"factor {name!r}: signal_type={signal_type!r} not in closed vocabulary "
            f"{sorted(_VALID_SIGNAL_TYPES)}"
        )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if name in FACTOR_REGISTRY:
            raise ValueError(f"factor already registered: {name!r}")

        sig = inspect.signature(func)
        params = sig.parameters
        missing = [col for col in inputs if col not in params]
        if missing:
            raise ValueError(
                f"factor {name!r}: declared inputs {missing} not in function signature "
                f"{list(params)}"
            )

        FACTOR_REGISTRY[name] = FactorSpec(
            name=name,
            func=func,
            inputs=list(inputs),
            default_params=dict(defaults),
            alpha_horizon_bars=alpha_horizon_bars,
            bar_interval=bar_interval,
            signal_type=signal_type,
        )
        return func

    return decorator


def compute(name: str, **kwargs: Any) -> Any:
    """Registry dispatch. Forwards only kwargs whose name is in spec.inputs or
    matches a declared parameter in the function signature (hyperparameters).

    Extra kwargs not in the function signature are silently dropped — this
    allows the engine to pass all OHLCV columns to every factor without
    TypeError for factors that only declared `inputs=["close"]`.
    """
    if name not in FACTOR_REGISTRY:
        raise KeyError(f"unknown factor: {name!r}")

    spec = FACTOR_REGISTRY[name]
    sig = inspect.signature(spec.func)
    accepts_varkw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )

    if accepts_varkw:
        forward = dict(kwargs)
    else:
        allowed = set(sig.parameters.keys())
        forward = {k: v for k, v in kwargs.items() if k in allowed}

    return spec.func(**forward)


def list_factors() -> list[str]:
    """Return sorted list of registered factor names."""
    return sorted(FACTOR_REGISTRY.keys())

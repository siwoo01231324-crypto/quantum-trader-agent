"""Factor registry: name -> callable mapping with signature-validated dispatch."""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


DEFAULT_FACTOR_SET: str = "v1"


@dataclass
class FactorSpec:
    name: str
    func: Callable[..., Any]
    inputs: list[str]
    default_params: dict[str, Any] = field(default_factory=dict)


FACTOR_REGISTRY: dict[str, FactorSpec] = {}


def register(name: str, *, inputs: list[str], **defaults: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: @register("rsi", inputs=["close"], period=14).

    Validates at registration time that every entry in `inputs` appears in the
    decorated function's signature. Extra parameters (hyperparameters like
    `window`) are allowed but not required to match `inputs`.
    """
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

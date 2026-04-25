"""Load AsyncStrategyOrchestrator from a YAML config file.

Not callable from LLM tool surface (CLAUDE.md invariant #6).
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from portfolio._async_orchestrator import AsyncStrategyOrchestrator
from portfolio._strategy_adapter import _StrategyAdapter
from risk.dsl import Policy


def _import_class(dotted: str) -> type:
    module_path, _, class_name = dotted.rpartition(".")
    if not module_path:
        raise ImportError(f"Invalid class path (must be dotted): {dotted!r}")
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        raise ImportError(f"Cannot import module {module_path!r}: {exc}") from exc
    try:
        return getattr(module, class_name)
    except AttributeError as exc:
        raise ImportError(f"Class {class_name!r} not found in {module_path!r}") from exc


def _resolve_kwargs(raw_kwargs: dict[str, Any], yaml_dir: Path) -> dict[str, Any]:
    """Resolve special kwargs — metalabeler.load_path → MetaLabeler instance."""
    kwargs = dict(raw_kwargs)
    if "metalabeler" in kwargs:
        meta_cfg = kwargs["metalabeler"]
        if not isinstance(meta_cfg, dict) or "load_path" not in meta_cfg:
            raise ValueError(
                "kwargs.metalabeler must be a mapping with 'load_path' key"
            )
        load_path = Path(meta_cfg["load_path"])
        if not load_path.is_absolute():
            load_path = yaml_dir / load_path
        from ml.meta_labeler import MetaLabeler
        try:
            kwargs["metalabeler"] = MetaLabeler.load(load_path)
        except Exception as exc:
            raise RuntimeError(
                f"MetaLabeler.load({load_path}) failed: {exc}"
            ) from exc
    return kwargs


def load_orchestrator_from_yaml(
    path: Path,
    policy: Policy,
) -> AsyncStrategyOrchestrator:
    """Parse *path* and return a fully registered AsyncStrategyOrchestrator.

    Raises
    ------
    ValueError
        Duplicate strategy_id in the YAML file.
    ImportError
        Unknown class string.
    RuntimeError
        MetaLabeler.load failure (fail-fast, no silent fallback).
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    entries = config.get("strategies", [])
    seen_ids: set[str] = set()

    orch = AsyncStrategyOrchestrator(policy)

    for entry in entries:
        sid: str = entry["id"]
        if sid in seen_ids:
            raise ValueError(
                f"Duplicate strategy_id {sid!r} in {path}. "
                "Each strategy_id must be unique."
            )
        seen_ids.add(sid)

        cls = _import_class(entry["class"])
        raw_kwargs: dict[str, Any] = entry.get("kwargs", {}) or {}
        kwargs = _resolve_kwargs(raw_kwargs, path.parent)

        strategy = cls(**kwargs)
        orch.register_strategy(sid, _StrategyAdapter(strategy))
        orch.register_strategy_returns(sid, pd.Series(dtype=float))

    return orch

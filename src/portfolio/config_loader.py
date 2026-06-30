"""Load AsyncStrategyOrchestrator from a YAML config file.

Not callable from LLM tool surface (CLAUDE.md invariant #6).
"""
from __future__ import annotations

import importlib
import inspect
import logging
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml

from portfolio._async_orchestrator import AsyncStrategyOrchestrator
from portfolio._strategy_adapter import _StrategyAdapter
from risk.dsl import Policy

logger = logging.getLogger(__name__)


class _MetalabelerArtifactMissing(Exception):
    """Raised internally when MetaLabeler.load fails so caller can choose skip vs raise."""


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
    """Resolve special kwargs — metalabeler.load_path → MetaLabeler instance.

    Raises _MetalabelerArtifactMissing when MetaLabeler.load fails (artifact
    file missing, manifest malformed, etc). Outer loop decides skip vs raise
    based on the on_metalabeler_missing policy.
    """
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
            raise _MetalabelerArtifactMissing(
                f"MetaLabeler.load({load_path}) failed: {exc}"
            ) from exc
    return kwargs


def load_orchestrator_from_yaml(
    path: Path,
    policy: Policy,
    *,
    on_metalabeler_missing: Literal["raise", "skip"] = "raise",
) -> AsyncStrategyOrchestrator:
    """Parse *path* and return a fully registered AsyncStrategyOrchestrator.

    Parameters
    ----------
    on_metalabeler_missing : {"raise", "skip"}, default "raise"
        Behaviour when a strategy entry references a metalabeler artifact that
        is missing or malformed.

        - ``"raise"`` (default, preserves #94 fail-fast contract): translates
          the internal artifact-missing signal into a ``RuntimeError``. Used by
          tests and CI that demand fully-loaded orchestrators.
        - ``"skip"``: log a warning, drop only the affected entry, continue
          loading the remaining strategies. Used by ``src.live.loop`` so that
          a missing model file does not zero-out the entire orchestrator
          (#177 EXE-on-fresh-machine path).

    Raises
    ------
    ValueError
        Duplicate strategy_id in the YAML file.
    ImportError
        Unknown class string.
    RuntimeError
        MetaLabeler.load failure when ``on_metalabeler_missing="raise"``.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    entries = config.get("strategies", []) or []
    seen_ids: set[str] = set()

    # #238 Item 3b — optional top-level `orchestrator:` block arms the
    # duplicate-order backstop in the live deployment (qta.exe loads this
    # path). Absent key → 0.0 → bit-identical (every existing yaml/test).
    orch_cfg = config.get("orchestrator", {}) or {}
    min_order_interval_sec = float(orch_cfg.get("min_order_interval_sec", 0.0))
    # 선점 우선 cross-strategy 종목중복 차단 (2026-07-01). swing 롱·숏 동시운용
    # (투매반등 롱 + macross 데드숏) 시 같은 종목 네팅 사고 방지. 기본 False
    # = bit-identical (모든 기존 yaml/test). swing_mainnet.yaml 이 opt-in.
    cross_strategy_symbol_lock = bool(
        orch_cfg.get("cross_strategy_symbol_lock", False)
    )

    orch = AsyncStrategyOrchestrator(
        policy, min_order_interval_sec=min_order_interval_sec,
        cross_strategy_symbol_lock=cross_strategy_symbol_lock,
    )

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
        try:
            kwargs = _resolve_kwargs(raw_kwargs, path.parent)
        except _MetalabelerArtifactMissing as exc:
            if on_metalabeler_missing == "skip":
                logger.warning(
                    "config_loader.metalabeler_artifact_missing strategy_id=%s "
                    "skipping entry. Detail: %s",
                    sid, exc,
                )
                continue
            raise RuntimeError(str(exc)) from exc

        strategy = cls(**kwargs)
        # Async strategies (#78 AsyncStrategy Protocol) have a coroutine on_bar
        # accepting ctx directly; skip the legacy sync adapter for those so the
        # orchestrator forwards ctx["market_snapshot"] unmodified.
        if inspect.iscoroutinefunction(getattr(strategy, "on_bar", None)):
            orch.register_strategy(sid, strategy)
        else:
            orch.register_strategy(sid, _StrategyAdapter(strategy))
        orch.register_strategy_returns(sid, pd.Series(dtype=float))

    return orch

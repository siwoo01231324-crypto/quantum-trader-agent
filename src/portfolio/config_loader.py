"""Load AsyncStrategyOrchestrator from a YAML config file.

Not callable from LLM tool surface (CLAUDE.md invariant #6).
"""
from __future__ import annotations

import importlib
import inspect
import logging
import os
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
    # 전체 *증거금* 노출 상한 (2026-07-01). 열린 포지션 default_size 합 ÷ 레버리지
    # = 증거금% 캡. 증거금 기준이라 레버(QTA_TARGET_LEVERAGE) 1x↔10x 바뀌어도 캡
    # 고정. 0.0(기본)=무제한 bit-identical. 레버는 executor 와 동일 env 를 읽어 정합.
    max_total_margin_pct = float(orch_cfg.get("max_total_margin_pct", 0.0))
    try:
        _lev = float(os.environ.get("QTA_TARGET_LEVERAGE", "") or 1.0)
    except (TypeError, ValueError):
        _lev = 1.0
    total_leverage = _lev if _lev >= 1.0 else 1.0

    orch = AsyncStrategyOrchestrator(
        policy, min_order_interval_sec=min_order_interval_sec,
        cross_strategy_symbol_lock=cross_strategy_symbol_lock,
        max_total_margin_pct=max_total_margin_pct,
        total_leverage=total_leverage,
    )

    # 전략별 레버리지 오버라이드 (2026-07-05) — yaml entry 의 top-level ``leverage``
    # (kwargs 아님). 모아서 QTA_STRATEGY_LEVERAGE env 로 전달 → executor 가 주문별로
    # 그 전략 레버 설정. 예: macross 만 5x, 투매·터틀 미지정(전역/1x). 명목만 키우고
    # 증거금 유지하는 용도 (default_size 상향과 함께).
    _strategy_leverage: dict[str, int] = {}
    # observe_only 관찰 전용 전략 (2026-07-08) — yaml entry top-level
    # ``observe_only: true`` 면 orchestrator 에 등록은 하되 진입 신호를 기록만
    # 하고 실주문 안 냄(HARD GUARD in run_bar). macross pause 중 신호 데이터
    # 수집용. 미지정(기본) = 정상 매매.
    _observe_only: set[str] = set()

    for entry in entries:
        sid: str = entry["id"]
        if sid in seen_ids:
            raise ValueError(
                f"Duplicate strategy_id {sid!r} in {path}. "
                "Each strategy_id must be unique."
            )
        seen_ids.add(sid)
        if bool(entry.get("observe_only", False)):
            _observe_only.add(sid)
        _lev_ov = entry.get("leverage")
        if _lev_ov is not None:
            try:
                _lv = int(_lev_ov)
                if _lv > 0:
                    _strategy_leverage[sid] = _lv
            except (TypeError, ValueError):
                logger.warning(
                    "config_loader.bad_leverage strategy_id=%s leverage=%r skipped",
                    sid, _lev_ov,
                )

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

    # 전략별 레버 → executor 가 읽는 env 로 전달 (있을 때만). 기존 env 는 yaml 이
    # 우선(override) — config 가 truth source.
    if _strategy_leverage:
        os.environ["QTA_STRATEGY_LEVERAGE"] = ",".join(
            f"{k}:{v}" for k, v in _strategy_leverage.items()
        )
        # 증거금 캡이 전략별 레버 반영하도록 orch 에도 전달 (macross 5x → 캡 과조기
        # 차단 방지). 생성자는 루프 前이라 여기서 세팅.
        orch._strategy_leverage = {
            k: max(1.0, float(v)) for k, v in _strategy_leverage.items()
        }
        logger.info(
            "config_loader.strategy_leverage set QTA_STRATEGY_LEVERAGE=%s",
            os.environ["QTA_STRATEGY_LEVERAGE"],
        )

    # 관찰 전용 전략 → orchestrator HARD GUARD 집합에 주입 (있을 때만).
    if _observe_only:
        orch._observe_only = set(_observe_only)
        logger.info(
            "config_loader.observe_only strategies=%s (진입 신호 기록만, 실주문 0)",
            sorted(_observe_only),
        )

    return orch

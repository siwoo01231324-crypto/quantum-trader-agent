"""#238 follow-up — root-cause wiring for KIS-inert / permanent history.

Covers the live_run.py + loop.py seams:
  - `run_shadow_loop` invokes `config.on_snapshot_builder_ready(builder)`
    once, so the dashboard's `state.snapshot_builder` is set (venue INERT
    visibility). Default None = no-op (legacy/tests untouched).
  - `_wire_balance_provider(cfg, existing=...)` reuses the dashboard's
    already-warm AccountInfoProvider instead of a cold per-pipeline one
    (the actual KIS-inert break under REST contention). No `existing`
    arg → byte-identical to the legacy fresh-instance behaviour.
  - `state.log_dir` is set from the pipeline's wal_path so trade history /
    strategy positions are PERMANENT (Issue 2 seam).
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.live.loop import ShadowConfig, run_shadow_loop
from src.live.types import Tick


def _ticks(symbol: str, n: int = 2) -> list[Tick]:
    out: list[Tick] = []
    for i in range(n):
        ts = f"2026-05-11T05:{i:02d}:00+00:00"
        out.append(Tick(symbol=symbol, price=Decimal("80000"),
                         qty=Decimal("1"), ts=ts, server_ts=ts))
    return out


def _config(tmp_path: Path, *, mock_ticks, max_iterations=2) -> ShadowConfig:
    return ShadowConfig(
        symbols=["005930"],
        wal_path=tmp_path / "wal.jsonl",
        lock_path=tmp_path / ".live_loop.lock",
        initial_balance=Decimal("1000000"),
        production_yaml=tmp_path / "missing.yaml",  # empty-orchestrator fallback
        max_iterations=max_iterations,
        broker_mode="paper-only",
        feed_mode="mock",
        schedule="always",
        mock_ticks=mock_ticks,
    )


class TestSnapshotBuilderReadyCallback:
    @pytest.mark.asyncio
    async def test_callback_fires_once_with_builder(self, tmp_path):
        seen = []
        cfg = _config(tmp_path, mock_ticks=_ticks("005930", 2))
        cfg.on_snapshot_builder_ready = lambda sb: seen.append(sb)

        await run_shadow_loop(cfg)

        assert len(seen) == 1
        # The object must be the live SnapshotBuilder (carries the status map).
        assert hasattr(seen[0], "last_equity_status")

    @pytest.mark.asyncio
    async def test_no_callback_is_safe_noop(self, tmp_path):
        cfg = _config(tmp_path, mock_ticks=_ticks("005930", 1))
        # on_snapshot_builder_ready stays None (default) — must not raise.
        await run_shadow_loop(cfg)

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_kill_loop(self, tmp_path):
        def _boom(_sb):
            raise RuntimeError("ready hook boom")

        cfg = _config(tmp_path, mock_ticks=_ticks("005930", 1))
        cfg.on_snapshot_builder_ready = _boom
        # A broken ready-hook must not crash the trading loop.
        await run_shadow_loop(cfg)


class TestWireBalanceProviderReuse:
    def test_reuses_existing_provider_instance(self):
        from scripts.live_run import _wire_balance_provider

        class _Cfg:
            balance_provider = None

        sentinel = object()
        cfg = _Cfg()
        _wire_balance_provider(cfg, existing=sentinel)
        assert cfg.balance_provider is sentinel

    def test_builds_fresh_when_no_existing(self):
        from scripts.live_run import _wire_balance_provider
        from src.dashboard.account_info import AccountInfoProvider

        class _Cfg:
            balance_provider = None

        cfg = _Cfg()
        _wire_balance_provider(cfg)  # legacy call form unchanged
        assert isinstance(cfg.balance_provider, AccountInfoProvider)

    def test_none_cfg_is_noop(self):
        from scripts.live_run import _wire_balance_provider
        _wire_balance_provider(None)  # must not raise


class TestLogDirSeamPermanence:
    @pytest.mark.asyncio
    async def test_pipeline_sets_state_log_dir_for_permanent_history(
        self, tmp_path, monkeypatch,
    ):
        """_run_pipeline must set dashboard_state.log_dir = wal.parent.parent
        so trade history / strategy positions survive across runs."""
        import scripts.live_run as lr

        captured = {}

        # Intercept the DashboardState the pipeline builds.
        from src.dashboard.app import DashboardState

        orig_init = DashboardState.__init__

        def _spy_init(self, *a, **k):
            orig_init(self, *a, **k)
            captured["state"] = self

        monkeypatch.setattr(DashboardState, "__init__", _spy_init)

        run_dir = tmp_path / "logs" / "live" / "20260101T000000Z"
        run_dir.mkdir(parents=True)
        args = lr.parse_args([
            "--symbols", "005930", "--broker", "paper-only",
            "--feed", "mock", "--mock-bars", "1", "--max-iterations", "1",
            "--dashboard-port", "0", "--run-id", "20260101T000000Z",
            "--log-dir", str(tmp_path / "logs" / "live"),
        ])
        cfg = lr._build_config(args)
        cfg.mock_ticks = lr._build_mock_ticks(args.symbols, args.mock_bars)
        import asyncio
        await lr._run_pipeline(
            cfg, None, 0, lr.logging.getLogger("t"), 0.0,
            auto_open_browser=False,
        )
        state = captured["state"]
        assert state.log_dir is not None
        assert Path(state.log_dir) == (tmp_path / "logs" / "live")
        # And the ready-callback wired snapshot_builder onto the same state.
        assert state.snapshot_builder is not None
        assert hasattr(state.snapshot_builder, "last_equity_status")

"""run_shadow_loop schedule gate integration test (#216 US-003).

Validates that ``run_shadow_loop`` invokes ``wait_for_session_fn`` with the
configured schedule before any side-effecting startup work (ProcessLock,
WAL, snapshot warmup, feed connect). The injected wait function raises
``RuntimeError`` to cut the loop short — we only need to assert the gate
fires with the right argument; deeper integration is covered by the
unit tests on ``wait_until_session_open`` itself.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.live.loop import ShadowConfig, run_shadow_loop


def _make_config(tmp_path: Path, schedule: str) -> ShadowConfig:
    return ShadowConfig(
        symbols=["005930"],
        wal_path=tmp_path / "wal.jsonl",
        lock_path=tmp_path / ".live_loop.lock",
        initial_balance=Decimal("100000"),
        production_yaml=tmp_path / "missing.yaml",  # forces fallback path
        max_iterations=0,
        broker_mode="paper-only",
        feed_mode="mock",
        schedule=schedule,
    )


class TestScheduleGateIntegration:

    @pytest.mark.asyncio
    async def test_run_shadow_loop_invokes_gate_with_krx(self, tmp_path):
        called_with: list[str] = []

        async def fake_wait(schedule, **kwargs):
            called_with.append(schedule)
            raise RuntimeError("schedule gate observed; aborting test early")

        config = _make_config(tmp_path, "krx")
        with pytest.raises(RuntimeError, match="schedule gate observed"):
            await run_shadow_loop(config, wait_for_session_fn=fake_wait)

        assert called_with == ["krx"]

    @pytest.mark.asyncio
    async def test_run_shadow_loop_invokes_gate_with_always(self, tmp_path):
        called_with: list[str] = []

        async def fake_wait(schedule, **kwargs):
            called_with.append(schedule)
            raise RuntimeError("stop after gate")

        config = _make_config(tmp_path, "always")
        with pytest.raises(RuntimeError, match="stop after gate"):
            await run_shadow_loop(config, wait_for_session_fn=fake_wait)

        assert called_with == ["always"]

    @pytest.mark.asyncio
    async def test_gate_fires_before_process_lock(self, tmp_path):
        """Gate must run before ProcessLock acquisition — the lock file must
        not exist when the gate raises (i.e. gate precedes lock setup)."""

        async def fake_wait(schedule, **kwargs):
            raise RuntimeError("gate first")

        config = _make_config(tmp_path, "krx")
        with pytest.raises(RuntimeError, match="gate first"):
            await run_shadow_loop(config, wait_for_session_fn=fake_wait)

        # Lock file must not have been created — i.e. ProcessLock.acquire never
        # ran because the gate aborted earlier.
        assert not config.lock_path.exists()


class TestBuildConfigPropagatesSchedule:
    """live_run._build_config wires args.schedule → ShadowConfig.schedule."""

    def test_args_schedule_krx_propagates(self):
        # Import here to avoid module-level coupling for tests that do not
        # exercise live_run wiring.
        import argparse
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from importlib import import_module
        live_run = import_module("live_run") if "live_run" in sys.modules else None
        if live_run is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "live_run_mod",
                Path(__file__).resolve().parents[2] / "scripts" / "live_run.py",
            )
            live_run = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(live_run)  # type: ignore[union-attr]

        args = argparse.Namespace(
            symbols=["005930"],
            run_id="test-run",
            log_dir="logs/test",
            initial_balance="100000",
            production_yaml="configs/orchestrator/production.yaml",
            max_iterations=None,
            broker="paper-only",
            feed="mock",
            schedule="krx",
        )
        config = live_run._build_config(args)
        assert config.schedule == "krx"

    def test_args_schedule_always_propagates(self):
        import argparse
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "live_run_mod",
            Path(__file__).resolve().parents[2] / "scripts" / "live_run.py",
        )
        live_run = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(live_run)  # type: ignore[union-attr]

        args = argparse.Namespace(
            symbols=["BTCUSDT"],
            run_id=None,
            log_dir="logs/test",
            initial_balance="100000",
            production_yaml="configs/orchestrator/production.yaml",
            max_iterations=None,
            broker="paper-only",
            feed="binance",
            schedule="always",
        )
        config = live_run._build_config(args)
        assert config.schedule == "always"

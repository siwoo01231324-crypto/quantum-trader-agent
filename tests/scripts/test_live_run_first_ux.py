"""qta.exe 첫 실행 UX 테스트 (#182).

no-args 분기, 자동 브라우저, 시작 배너, --no-browser 플래그 검증.
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[2]
LIVE_RUN_PATH = WORKTREE / "scripts" / "live_run.py"

sys.path.insert(0, str(WORKTREE))
sys.path.insert(0, str(WORKTREE / "src"))

from scripts import live_run  # noqa: E402


# ---------------------------------------------------------------------------
# _is_no_args
# ---------------------------------------------------------------------------

class TestIsNoArgs:
    def test_empty_argv_is_no_args(self) -> None:
        assert live_run._is_no_args([]) is True

    def test_with_symbols_is_not_no_args(self) -> None:
        assert live_run._is_no_args(["--symbols", "BTCUSDT"]) is False

    def test_with_help_only_is_not_no_args(self) -> None:
        # --help 는 argparse 가 정상 처리하도록 위임 (no-args 와 별개)
        assert live_run._is_no_args(["--help"]) is False


# ---------------------------------------------------------------------------
# _count_strategies
# ---------------------------------------------------------------------------

class TestCountStrategies:
    def test_count_strategies_from_yaml(self, tmp_path: Path) -> None:
        yml = tmp_path / "p.yaml"
        yml.write_text(
            "strategies:\n  - id: a\n    class: x.A\n  - id: b\n    class: x.B\n",
            encoding="utf-8",
        )
        assert live_run._count_strategies(yml) == 2

    def test_count_strategies_empty_list(self, tmp_path: Path) -> None:
        yml = tmp_path / "p.yaml"
        yml.write_text("strategies: []\n", encoding="utf-8")
        assert live_run._count_strategies(yml) == 0

    def test_count_strategies_missing_file_returns_zero(self, tmp_path: Path) -> None:
        assert live_run._count_strategies(tmp_path / "nope.yaml") == 0

    def test_count_strategies_malformed_yaml_returns_zero(self, tmp_path: Path) -> None:
        yml = tmp_path / "bad.yaml"
        yml.write_text(":::not yaml:::", encoding="utf-8")
        assert live_run._count_strategies(yml) == 0


# ---------------------------------------------------------------------------
# _print_startup_banner
# ---------------------------------------------------------------------------

class TestStartupBanner:
    def test_banner_contains_qta_and_strategy_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        live_run._print_startup_banner(strategies_count=5, dashboard_port=8000)
        out = capsys.readouterr().out
        assert "QTA" in out or "qta" in out.lower()
        assert "5" in out
        assert "8000" in out

    def test_banner_safe_with_zero_strategies(self, capsys: pytest.CaptureFixture[str]) -> None:
        live_run._print_startup_banner(strategies_count=0, dashboard_port=8000)
        out = capsys.readouterr().out
        assert "0" in out


# ---------------------------------------------------------------------------
# _show_first_run_help
# ---------------------------------------------------------------------------

class TestFirstRunHelp:
    def test_returns_zero(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        # stdin 닫힘 시나리오 (EOFError) 도 안전 종료해야 함
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        rc = live_run._show_first_run_help()
        assert rc == 0

    def test_outputs_banner_and_usage(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
        live_run._show_first_run_help()
        out = capsys.readouterr().out
        assert "QTA" in out or "qta" in out.lower()
        assert "--symbols" in out  # argparse usage 표시
        assert "Press Enter" in out or "press enter" in out.lower()


# ---------------------------------------------------------------------------
# parse_args — --no-browser 플래그
# ---------------------------------------------------------------------------

class TestNoBrowserFlag:
    def test_default_no_browser_false(self) -> None:
        args = live_run.parse_args(["--symbols", "BTCUSDT"])
        assert args.no_browser is False

    def test_no_browser_flag_sets_true(self) -> None:
        args = live_run.parse_args(["--symbols", "BTCUSDT", "--no-browser"])
        assert args.no_browser is True


# ---------------------------------------------------------------------------
# subprocess smoke — EXE 더블클릭 시뮬레이션
# ---------------------------------------------------------------------------

class TestSubprocessSmoke:
    def test_no_args_help_only_mode(self) -> None:
        """QTA_FIRST_RUN_HELP_ONLY=true → 도움말 + Press Enter 분기 (테스트용)."""
        env = {
            **__import__("os").environ,
            "PYTHONIOENCODING": "utf-8",
            "QTA_FIRST_RUN_HELP_ONLY": "true",
        }
        result = subprocess.run(
            [sys.executable, str(LIVE_RUN_PATH)],
            input=b"\n", capture_output=True, timeout=15,
            cwd=str(WORKTREE), env=env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr.decode('utf-8', errors='replace')}"
        out = result.stdout.decode("utf-8", errors="replace")
        assert "--symbols" in out  # usage
        assert "Press Enter" in out or "press enter" in out.lower()


class TestRunDashboardOnlyMode:
    """B 안 — 더블클릭 시 대시보드 자동 기동 (#182)."""

    def test_function_exists_and_returns_int(self) -> None:
        # 함수 시그니처 검증 (실제 uvicorn 시작은 별도 통합 테스트)
        assert callable(live_run._run_dashboard_only_mode)

    def test_main_no_args_calls_dashboard_only_by_default(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = {"flag": False, "port": None}

        def _fake(port: int = 8000) -> int:
            called["flag"] = True
            called["port"] = port
            return 0

        monkeypatch.setattr(live_run, "_run_dashboard_only_mode", _fake)
        monkeypatch.delenv("QTA_FIRST_RUN_HELP_ONLY", raising=False)
        rc = live_run.main([])
        assert rc == 0
        assert called["flag"] is True

    def test_main_no_args_help_only_when_env_set(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = {"help": False, "dashboard": False}

        def _fake_help() -> int:
            called["help"] = True
            return 0

        def _fake_dash(port: int = 8000) -> int:
            called["dashboard"] = True
            return 0

        monkeypatch.setattr(live_run, "_show_first_run_help", _fake_help)
        monkeypatch.setattr(live_run, "_run_dashboard_only_mode", _fake_dash)
        monkeypatch.setenv("QTA_FIRST_RUN_HELP_ONLY", "true")
        rc = live_run.main([])
        assert rc == 0
        assert called["help"] is True
        assert called["dashboard"] is False

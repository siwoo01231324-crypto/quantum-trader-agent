"""Tests for scripts/telegram_control.py — Telegram bidirectional bot (#126).

Covers:
1. Command parsing (/cmd args, @BotName suffix stripping)
2. chat_id whitelist enforcement
3. /kill — POST kill-switch trigger
4. /release — 2-step confirmation flow
5. /status — fetches PnL + limits + ks state
6. /policy — parses policy.yaml
7. /help — static reply
8. Unknown command — rejected
9. Audit WAL append on every command
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import responses

# Add repo root + scripts/ to path for the script's `import requests` etc.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import scripts.telegram_control as tc  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_pending():
    tc.reset_pending_release_for_test()
    yield
    tc.reset_pending_release_for_test()


def _make_config(tmp_path: Path | None = None, *, allowed: tuple[int, ...] = (12345,),
                 policy: dict | None = None) -> tc.BotConfig:
    audit_wal = (tmp_path / "wal.jsonl") if tmp_path else None
    policy_path = None
    if tmp_path is not None and policy is not None:
        import yaml
        policy_path = tmp_path / "policy.yaml"
        policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    return tc.BotConfig(
        token="testtoken",
        allowed_chat_ids=frozenset(allowed),
        dashboard_base_url="http://dashboard.test",
        audit_wal_path=audit_wal,
        policy_yaml_path=policy_path,
    )


def _msg(text: str, chat_id: int = 12345, user_id: int = 67890) -> dict:
    return {
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": user_id, "username": "tester"},
        "text": text,
    }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestParseCommand:
    def test_simple_command(self):
        assert tc.parse_command("/kill panic") == ("kill", "panic")

    def test_no_args(self):
        assert tc.parse_command("/status") == ("status", "")

    def test_strips_at_botname(self):
        assert tc.parse_command("/kill@QuantumBot reason") == ("kill", "reason")

    def test_lowercased(self):
        assert tc.parse_command("/KILL Reason") == ("kill", "Reason")

    def test_non_command_returns_empty(self):
        assert tc.parse_command("hello") == ("", "")

    def test_empty_text(self):
        assert tc.parse_command("") == ("", "")


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

class TestWhitelist:
    def test_allowed(self):
        config = _make_config(allowed=(12345,))
        assert tc.is_allowed_chat(config, 12345) is True

    def test_denied(self):
        config = _make_config(allowed=(12345,))
        assert tc.is_allowed_chat(config, 99999) is False

    def test_empty_whitelist_denies_all(self):
        config = _make_config(allowed=())
        assert tc.is_allowed_chat(config, 12345) is False


# ---------------------------------------------------------------------------
# /kill
# ---------------------------------------------------------------------------

class TestKill:
    @responses.activate
    def test_kill_triggers_dashboard(self):
        config = _make_config()
        responses.add(
            responses.POST, "http://dashboard.test/api/kill-switch/trigger",
            json={"ok": True, "reason": "manual"}, status=200,
        )
        result = tc.handle_kill(config, "panic mode", 12345, 67890)
        assert len(responses.calls) == 1
        assert result.accepted is True
        assert "비상정지 발동" in result.reply
        assert "panic mode" in result.reply

    @responses.activate
    def test_kill_dashboard_500(self):
        config = _make_config()
        responses.add(
            responses.POST, "http://dashboard.test/api/kill-switch/trigger",
            json={"error": "boom"}, status=500,
        )
        result = tc.handle_kill(config, "x", 12345, 67890)
        assert result.accepted is False
        assert result.reason == "http_500"

    @responses.activate
    def test_kill_dashboard_unreachable(self):
        import requests as req
        config = _make_config()
        responses.add(
            responses.POST, "http://dashboard.test/api/kill-switch/trigger",
            body=req.exceptions.ConnectionError("connection refused"),
        )
        result = tc.handle_kill(config, "x", 12345, 67890)
        assert result.accepted is False
        assert result.reason == "dashboard_unreachable"


# ---------------------------------------------------------------------------
# /release (2-step confirmation)
# ---------------------------------------------------------------------------

class TestRelease:
    def test_first_press_asks_confirm(self):
        config = _make_config()
        result = tc.handle_release(config, "", 12345, 67890)
        assert result.accepted is False
        assert result.reason == "awaiting_confirmation"
        assert "60초" in result.reply or "confirm" in result.reply.lower() or "한 번 더" in result.reply

    @responses.activate
    def test_second_press_within_window_executes(self):
        config = _make_config()
        responses.add(
            responses.POST, "http://dashboard.test/api/kill-switch/reset",
            json={"ok": True}, status=200,
        )
        # First press
        first = tc.handle_release(config, "", 12345, 67890)
        assert first.accepted is False
        # Second press (within window)
        second = tc.handle_release(config, "", 12345, 67890)
        assert second.accepted is True
        assert "해제" in second.reply

    @responses.activate
    def test_second_press_after_window_resets(self):
        config = _make_config()
        responses.add(
            responses.POST, "http://dashboard.test/api/kill-switch/reset",
            json={"ok": True}, status=200,
        )
        # First press
        tc.handle_release(config, "", 12345, 67890)
        # Simulate stale state by manipulating internal pending dict
        tc._pending_release[12345] = 0.0  # noqa: SLF001 — test only
        # Second press now treated as fresh first press → should ask confirm again
        result = tc.handle_release(config, "", 12345, 67890)
        assert result.accepted is False
        assert result.reason == "awaiting_confirmation"


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

class TestStatus:
    @responses.activate
    def test_status_fetches_three_endpoints(self):
        config = _make_config()
        responses.add(
            responses.GET, "http://dashboard.test/api/pnl",
            json={"realtime": 100.5, "daily": 50.0, "monthly": 1234.5}, status=200,
        )
        responses.add(
            responses.GET, "http://dashboard.test/api/limits",
            json={"per_trade": 0.3, "drawdown": 0.85}, status=200,
        )
        responses.add(
            responses.GET, "http://dashboard.test/api/kill-switch",
            json={"triggers": {"manual": False, "drawdown": False}}, status=200,
        )
        result = tc.handle_status(config, "", 12345, 67890)
        assert result.accepted is True
        assert "PnL" in result.reply
        assert "100.50" in result.reply or "100.5" in result.reply
        assert "🟢 normal" in result.reply

    @responses.activate
    def test_status_shows_active_killswitch(self):
        config = _make_config()
        responses.add(
            responses.GET, "http://dashboard.test/api/pnl",
            json={"realtime": 0, "daily": 0, "monthly": 0}, status=200,
        )
        responses.add(
            responses.GET, "http://dashboard.test/api/limits",
            json={"per_trade": 0.0}, status=200,
        )
        responses.add(
            responses.GET, "http://dashboard.test/api/kill-switch",
            json={"triggers": {"manual": True, "drawdown": False}}, status=200,
        )
        result = tc.handle_status(config, "", 12345, 67890)
        assert result.accepted is True
        assert "🔴" in result.reply
        assert "manual" in result.reply


# ---------------------------------------------------------------------------
# /policy
# ---------------------------------------------------------------------------

class TestPolicy:
    def test_policy_summary(self, tmp_path: Path):
        config = _make_config(
            tmp_path=tmp_path,
            policy={"per_trade": {"max_pct": 0.05}, "drawdown_limit": 0.15, "symbols": ["BTC", "ETH"]},
        )
        result = tc.handle_policy(config, "", 12345, 67890)
        assert result.accepted is True
        assert "per_trade" in result.reply
        assert "drawdown_limit" in result.reply
        assert "(2 items)" in result.reply  # symbols list

    def test_policy_missing(self, tmp_path: Path):
        config = _make_config(tmp_path=tmp_path)
        # No policy file written
        result = tc.handle_policy(config, "", 12345, 67890)
        assert result.accepted is False
        assert "missing" in result.reply.lower() or "❌" in result.reply


# ---------------------------------------------------------------------------
# Dispatch — auth + unknown command
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_dispatch_unauthorized(self):
        config = _make_config(allowed=(12345,))
        result = tc.dispatch(config, _msg("/status", chat_id=99999))
        assert result is not None
        assert result.accepted is False
        assert result.reason == "unauthorized"

    def test_dispatch_unknown_command(self):
        config = _make_config()
        result = tc.dispatch(config, _msg("/foobar"))
        assert result is not None
        assert result.accepted is False
        assert result.reason == "unknown_command"

    def test_dispatch_help(self):
        config = _make_config()
        result = tc.dispatch(config, _msg("/help"))
        assert result is not None
        assert result.accepted is True
        assert "/kill" in result.reply

    def test_dispatch_non_command_returns_none(self):
        config = _make_config()
        assert tc.dispatch(config, _msg("just chatting")) is None


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_audit_writes_jsonl(self, tmp_path: Path):
        wal = tmp_path / "wal.jsonl"
        result = tc.CommandResult(
            command="kill", args="reason", chat_id=12345, user_id=67890,
            accepted=True, reply="ok",
        )
        tc.write_audit_event(wal, result)
        assert wal.exists()
        line = wal.read_text(encoding="utf-8").strip()
        rec = json.loads(line)
        assert rec["event_type"] == "command_received"
        assert rec["payload"]["command"] == "kill"
        assert rec["payload"]["chat_id"] == 12345
        assert rec["payload"]["accepted"] is True

    def test_audit_swallows_io_errors(self, tmp_path: Path):
        # Write to a path whose parent cannot be created (use a file as dir).
        bad = tmp_path / "not_a_dir.txt"
        bad.write_text("blocking", encoding="utf-8")
        target = bad / "wal.jsonl"  # invalid because bad is a file
        result = tc.CommandResult(
            command="x", args="", chat_id=1, user_id=1, accepted=False, reply="",
        )
        # Must not raise.
        tc.write_audit_event(target, result)


class TestBuildConfigEnvFallback:
    """#216 — build_config_from_env 이 LIVE > QTA > legacy 우선순위로 토큰/chat_id 해석."""

    def _clear(self, monkeypatch):
        for v in (
            "TELEGRAM_LIVE_BOT_TOKEN", "TELEGRAM_LIVE_CHAT_ID",
            "TELEGRAM_QTA_BOT_TOKEN", "TELEGRAM_QTA_CHAT_ID",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        ):
            monkeypatch.delenv(v, raising=False)

    def _make_args(self):
        import argparse
        return argparse.Namespace(
            audit_wal=None,
            policy="configs/policy.yaml",
            dashboard="http://localhost:8000",
        )

    def test_legacy_only(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "legacy_tok")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
        cfg = tc.build_config_from_env(self._make_args())
        assert cfg.token == "legacy_tok"
        assert cfg.allowed_chat_ids == frozenset({111})

    def test_qta_overrides_legacy(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "legacy_tok")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
        monkeypatch.setenv("TELEGRAM_QTA_BOT_TOKEN", "qta_tok")
        monkeypatch.setenv("TELEGRAM_QTA_CHAT_ID", "222")
        cfg = tc.build_config_from_env(self._make_args())
        assert cfg.token == "qta_tok"
        assert cfg.allowed_chat_ids == frozenset({222})

    def test_live_overrides_all(self, monkeypatch):
        """#216 운영 표준: TELEGRAM_LIVE_* 가 1순위."""
        self._clear(monkeypatch)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "legacy_tok")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
        monkeypatch.setenv("TELEGRAM_QTA_BOT_TOKEN", "qta_tok")
        monkeypatch.setenv("TELEGRAM_QTA_CHAT_ID", "222")
        monkeypatch.setenv("TELEGRAM_LIVE_BOT_TOKEN", "live_tok")
        monkeypatch.setenv("TELEGRAM_LIVE_CHAT_ID", "333")
        cfg = tc.build_config_from_env(self._make_args())
        assert cfg.token == "live_tok"
        assert cfg.allowed_chat_ids == frozenset({333})

    def test_multi_chat_csv_still_parsed(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("TELEGRAM_LIVE_BOT_TOKEN", "live_tok")
        monkeypatch.setenv("TELEGRAM_LIVE_CHAT_ID", "100, 200, 300")
        cfg = tc.build_config_from_env(self._make_args())
        assert cfg.allowed_chat_ids == frozenset({100, 200, 300})

    def test_empty_env_yields_empty_config(self, monkeypatch):
        self._clear(monkeypatch)
        cfg = tc.build_config_from_env(self._make_args())
        assert cfg.token == ""
        assert cfg.allowed_chat_ids == frozenset()


_TEST_BASE = "http://dashboard.test"


class TestTradingStatusCommands:
    """#221 — /today /positions /fills /account 명령어 단위 테스트."""

    @responses.activate
    def test_today_aggregates_pnl_and_strategies(self):
        cfg = _make_config()
        responses.add(
            responses.GET, f"{_TEST_BASE}/api/pnl",
            json={"realtime": 12.5, "daily": 100.0, "monthly": 500.0,
                  "by_strategy": {"momo-btc-v2": 80.0, "momo-kis-v1": 20.0}},
            status=200,
        )
        responses.add(
            responses.GET, f"{_TEST_BASE}/api/strategies",
            json=[{"strategy_id": "momo-btc-v2", "enabled": True},
                  {"strategy_id": "momo-kis-v1", "enabled": False}],
            status=200,
        )
        result = tc.handle_today(cfg, "", chat_id=12345, user_id=1)
        assert result.accepted
        assert "오늘" in result.reply
        assert "100.00 KRW" in result.reply
        assert "momo-btc-v2: 80.00" in result.reply
        assert "Strategies active: 1/2" in result.reply

    @responses.activate
    def test_positions_lists_open_positions(self):
        cfg = _make_config()
        responses.add(
            responses.GET, f"{_TEST_BASE}/api/strategies",
            json=[
                {"strategy_id": "momo-kis-v1", "positions": {"005930": "10", "035720": "5"}},
                {"strategy_id": "idle", "positions": {}},
            ],
            status=200,
        )
        result = tc.handle_positions(cfg, "", chat_id=12345, user_id=1)
        assert result.accepted
        assert "momo-kis-v1" in result.reply
        assert "005930=10" in result.reply
        assert "035720=5" in result.reply

    @responses.activate
    def test_positions_no_open(self):
        cfg = _make_config()
        responses.add(
            responses.GET, f"{_TEST_BASE}/api/strategies",
            json=[{"strategy_id": "idle", "positions": {}}],
            status=200,
        )
        result = tc.handle_positions(cfg, "", chat_id=12345, user_id=1)
        assert result.accepted
        assert "no open positions" in result.reply

    @responses.activate
    def test_fills_returns_latest_n(self):
        cfg = _make_config()
        responses.add(
            responses.GET, f"{_TEST_BASE}/api/shadow_runs",
            json=[{"run_id": "run-001"}, {"run_id": "run-002"}],
            status=200,
        )
        responses.add(
            responses.GET, f"{_TEST_BASE}/api/shadow_runs/run-002",
            json={"fills": [
                {"ts": "2026-05-08T09:01:23.456Z", "symbol": "005930", "side": "buy",
                 "qty": "10", "price": "70000"},
                {"ts": "2026-05-08T09:02:00Z", "symbol": "035720", "side": "buy",
                 "qty": "5", "price": "55000"},
            ]},
            status=200,
        )
        result = tc.handle_fills(cfg, "5", chat_id=12345, user_id=1)
        assert result.accepted
        assert "run-002" in result.reply
        assert "005930" in result.reply
        assert "035720" in result.reply

    @responses.activate
    def test_fills_no_runs(self):
        cfg = _make_config()
        responses.add(
            responses.GET, f"{_TEST_BASE}/api/shadow_runs",
            json=[],
            status=200,
        )
        result = tc.handle_fills(cfg, "", chat_id=12345, user_id=1)
        assert result.accepted
        assert "no shadow runs" in result.reply

    @responses.activate
    def test_account_shows_balance(self):
        cfg = _make_config()
        responses.add(
            responses.GET, f"{_TEST_BASE}/api/account/info",
            json={"balance_krw": 1000000, "available_krw": 950000,
                  "equity_krw": 1010000, "currency": "KRW",
                  "broker": "kis-paper-shadow", "mode": "paper"},
            status=200,
        )
        result = tc.handle_account(cfg, "", chat_id=12345, user_id=1)
        assert result.accepted
        assert "1000000" in result.reply
        assert "kis-paper-shadow" in result.reply

    @responses.activate
    def test_account_dashboard_unreachable(self):
        cfg = _make_config()
        # No mock registered → connection error path
        result = tc.handle_account(cfg, "", chat_id=12345, user_id=1)
        assert not result.accepted
        assert "unreachable" in result.reply or "Connection" in result.reply

    def test_help_includes_new_commands(self):
        cfg = _make_config()
        result = tc.handle_help(cfg, "", chat_id=12345, user_id=1)
        assert result.accepted
        for cmd in ("/today", "/positions", "/fills", "/account"):
            assert cmd in result.reply

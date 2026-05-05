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

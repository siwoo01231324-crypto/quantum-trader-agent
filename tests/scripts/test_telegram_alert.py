"""Unit tests for scripts/telegram_alert.py (#133 Phase 2 운영)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import responses

# scripts/ 는 패키지가 아니라 importlib 로 직접 로드.
_HERE = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "telegram_alert", _HERE / "scripts" / "telegram_alert.py"
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)  # type: ignore[union-attr]

send_telegram = _MOD.send_telegram
is_critical_event = _MOD.is_critical_event
scan_new_lines = _MOD.scan_new_lines
summarize_report = _MOD.summarize_report
TELEGRAM_MAX_LEN = _MOD.TELEGRAM_MAX_LEN


@pytest.fixture
def telegram_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")


@responses.activate
def test_send_telegram_ok(telegram_env):
    responses.add(
        responses.POST,
        "https://api.telegram.org/bottest_token/sendMessage",
        json={"ok": True},
        status=200,
    )
    assert send_telegram("hello") is True
    assert len(responses.calls) == 1
    body = json.loads(responses.calls[0].request.body)
    assert body["chat_id"] == "12345"
    assert body["text"] == "hello"


@responses.activate
def test_send_telegram_skip_when_env_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    # No responses registered → would fail if it tried to call.
    assert send_telegram("hello") is False
    assert len(responses.calls) == 0


@responses.activate
def test_send_telegram_truncates_long(telegram_env):
    responses.add(
        responses.POST,
        "https://api.telegram.org/bottest_token/sendMessage",
        json={"ok": True},
        status=200,
    )
    long_text = "x" * (TELEGRAM_MAX_LEN + 500)
    assert send_telegram(long_text) is True
    body = json.loads(responses.calls[0].request.body)
    assert len(body["text"]) <= TELEGRAM_MAX_LEN
    assert "truncated" in body["text"]


@responses.activate
def test_send_telegram_api_error(telegram_env):
    responses.add(
        responses.POST,
        "https://api.telegram.org/bottest_token/sendMessage",
        json={"ok": False, "description": "Bad Request"},
        status=400,
    )
    assert send_telegram("hello") is False


def test_is_critical_event_mode_switched():
    is_crit, msg = is_critical_event({"event_type": "mode_switched", "from": "kis", "to": "paper"})
    assert is_crit
    assert "mode_switched" in msg


def test_is_critical_event_fill_anomaly():
    is_crit, msg = is_critical_event({"event_type": "fill_anomaly", "broker_order_id": "X1"})
    assert is_crit
    assert "fill_anomaly" in msg


def test_is_critical_event_kill_switch_reject():
    is_crit, msg = is_critical_event(
        {"event_type": "order_rejected", "reject_reason": "KILL_SWITCH"}
    )
    assert is_crit
    assert "kill_switch_tripped" in msg
    assert "KILL_SWITCH" in msg


def test_is_critical_event_normal_reject_skipped():
    # KILL_SWITCH 가 아닌 일반 reject 는 skip
    is_crit, _ = is_critical_event(
        {"event_type": "order_rejected", "reject_reason": "INSUFFICIENT_FUNDS"}
    )
    assert not is_crit


def test_is_critical_event_order_acked_skipped():
    is_crit, _ = is_critical_event({"event_type": "order_acked", "origin": "executor"})
    assert not is_crit


def test_is_critical_event_unknown_type_skipped():
    is_crit, _ = is_critical_event({"event_type": "tracking_sample"})
    assert not is_crit


def test_scan_new_lines_appends_only(tmp_path: Path):
    wal = tmp_path / "wal.jsonl"
    wal.write_text(
        json.dumps({"event_type": "order_acked"}) + "\n"
        + json.dumps({"event_type": "mode_switched"}) + "\n",
        encoding="utf-8",
    )
    cursor: dict[str, int] = {}

    first = scan_new_lines(wal, cursor)
    assert len(first) == 2
    assert first[0]["event_type"] == "order_acked"
    assert first[1]["event_type"] == "mode_switched"

    # 두 번째 호출은 신규 line 없음
    second = scan_new_lines(wal, cursor)
    assert second == []

    # append 후 새 line 만 반환
    with wal.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"event_type": "fill_anomaly"}) + "\n")
    third = scan_new_lines(wal, cursor)
    assert len(third) == 1
    assert third[0]["event_type"] == "fill_anomaly"


def test_scan_new_lines_skips_malformed(tmp_path: Path):
    wal = tmp_path / "wal.jsonl"
    wal.write_text(
        json.dumps({"event_type": "order_acked"}) + "\n"
        + "not-json-garbage\n"
        + json.dumps({"event_type": "mode_switched"}) + "\n",
        encoding="utf-8",
    )
    events = scan_new_lines(wal, {})
    assert len(events) == 2  # malformed line skipped


def test_scan_new_lines_missing_file(tmp_path: Path):
    assert scan_new_lines(tmp_path / "nonexistent.jsonl", {}) == []


def test_summarize_report_within_limits():
    text = "# Report\n\n" + "\n".join(f"line {i}" for i in range(100))
    summary = summarize_report(text, "2026-04-28.md")
    assert "2026-04-28.md" in summary
    assert "Daily Report" in summary
    # 60 lines + header → well under 4000 chars
    assert len(summary) < 2000


def test_summarize_report_long_lines_capped():
    # 매우 긴 line 이라도 60 줄 까지만 — 발송 시 send_telegram 이 추가 truncate.
    text = "\n".join(["x" * 200 for _ in range(100)])
    summary = summarize_report(text, "long.md")
    assert summary.count("\n") <= 65  # 60 + header/code fence ~3

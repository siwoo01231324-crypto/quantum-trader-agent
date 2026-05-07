import sys
import pytest
import requests

from src.observability.alerts import notify


def _make_post_mock(calls: list):
    def mock_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json})
    return mock_post


def _clear_telegram_env(monkeypatch):
    """#152: LIVE/QTA fallback 우선이라 legacy 경로 검증 시 모두 비워야 함."""
    for v in (
        "TELEGRAM_LIVE_BOT_TOKEN", "TELEGRAM_LIVE_CHAT_ID",
        "TELEGRAM_QTA_BOT_TOKEN", "TELEGRAM_QTA_CHAT_ID",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(v, raising=False)


# Test 1: SLACK only → 1 call
def test_slack_only(monkeypatch):
    calls = []
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
    _clear_telegram_env(monkeypatch)
    monkeypatch.setattr(requests, "post", _make_post_mock(calls))

    notify("warn", "test title", "test body", {"key": "val"})

    assert len(calls) == 1
    assert calls[0]["url"] == "https://hooks.slack.com/test"
    assert calls[0]["json"]["username"] == "metalabeler"
    assert "test title" in calls[0]["json"]["text"]


# Test 2: TELEGRAM only (legacy) → 1 call
def test_telegram_only(monkeypatch):
    calls = []
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot_token_123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat_456")
    monkeypatch.setattr(requests, "post", _make_post_mock(calls))

    notify("info", "tg title", "tg body")

    assert len(calls) == 1
    assert "bot_token_123" in calls[0]["url"]
    assert calls[0]["json"]["chat_id"] == "chat_456"


# Test 2b (#152): TELEGRAM_LIVE_* takes priority over legacy
def test_telegram_live_priority(monkeypatch):
    calls = []
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "legacy")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM_QTA_BOT_TOKEN", "qta")
    monkeypatch.setenv("TELEGRAM_QTA_CHAT_ID", "222")
    monkeypatch.setenv("TELEGRAM_LIVE_BOT_TOKEN", "live")
    monkeypatch.setenv("TELEGRAM_LIVE_CHAT_ID", "333")
    monkeypatch.setattr(requests, "post", _make_post_mock(calls))

    notify("warn", "live priority", "body")

    assert len(calls) == 1
    assert "live" in calls[0]["url"]
    assert calls[0]["json"]["chat_id"] == "333"


# Test 3: both → 2 calls
def test_both_channels(monkeypatch):
    calls = []
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test2")
    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "cid")
    monkeypatch.setattr(requests, "post", _make_post_mock(calls))

    notify("critical", "both title", "both body")

    assert len(calls) == 2


# Test 4: no env → 0 calls, stdout output
def test_no_env_stdout(monkeypatch, capsys):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    _clear_telegram_env(monkeypatch)

    calls = []
    monkeypatch.setattr(requests, "post", _make_post_mock(calls))

    notify("info", "fallback title", "fallback body")

    assert len(calls) == 0
    captured = capsys.readouterr()
    assert "fallback title" in captured.out


# Test 5: requests.post raises → swallowed, function returns normally
def test_post_exception_swallowed(monkeypatch):
    def raising_post(url, json=None, timeout=None):
        raise requests.exceptions.ConnectionError("network error")

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test3")
    _clear_telegram_env(monkeypatch)
    monkeypatch.setattr(requests, "post", raising_post)

    # Must not raise
    notify("critical", "crash title", "crash body")

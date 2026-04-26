import sys
import pytest
import requests

from src.observability.alerts import notify


def _make_post_mock(calls: list):
    def mock_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json})
    return mock_post


# Test 1: SLACK only → 1 call
def test_slack_only(monkeypatch):
    calls = []
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(requests, "post", _make_post_mock(calls))

    notify("warn", "test title", "test body", {"key": "val"})

    assert len(calls) == 1
    assert calls[0]["url"] == "https://hooks.slack.com/test"
    assert calls[0]["json"]["username"] == "metalabeler"
    assert "test title" in calls[0]["json"]["text"]


# Test 2: TELEGRAM only → 1 call
def test_telegram_only(monkeypatch):
    calls = []
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot_token_123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat_456")
    monkeypatch.setattr(requests, "post", _make_post_mock(calls))

    notify("info", "tg title", "tg body")

    assert len(calls) == 1
    assert "bot_token_123" in calls[0]["url"]
    assert calls[0]["json"]["chat_id"] == "chat_456"


# Test 3: both → 2 calls
def test_both_channels(monkeypatch):
    calls = []
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test2")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "cid")
    monkeypatch.setattr(requests, "post", _make_post_mock(calls))

    notify("critical", "both title", "both body")

    assert len(calls) == 2


# Test 4: no env → 0 calls, stdout output
def test_no_env_stdout(monkeypatch, capsys):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

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
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(requests, "post", raising_post)

    # Must not raise
    notify("critical", "crash title", "crash body")

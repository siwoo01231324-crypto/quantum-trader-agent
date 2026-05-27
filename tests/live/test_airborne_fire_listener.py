"""Unit tests for AirborneFireListener — daemon log polling + dedup."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from live.airborne_fire_listener import (
    AirborneFireListener,
    FireRecord,
    _parse_fire_line,
    _read_docker_logs,
)


# ── _parse_fire_line ────────────────────────────────────────────────────────
class TestParseFireLine:
    def test_long_fire(self):
        line = (
            "2026-05-23 02:00:33,327 INFO airborne_alert_daemon — "
            "FIRE CBRSUSDT long @ close=264.52 trigger=263.156"
        )
        rec = _parse_fire_line(line)
        assert rec is not None
        assert rec.symbol == "CBRSUSDT"
        assert rec.side == "long"
        assert rec.fire_close == 264.52
        assert rec.trigger == 263.156
        # KST 02:00 → UTC 전날 17:00 (PR #323 룰)
        assert rec.ts.isoformat() == "2026-05-22T17:00:33+00:00"

    def test_short_fire(self):
        line = (
            "2026-05-23 11:00:33,057 INFO airborne_alert_daemon — "
            "FIRE SKYAIUSDT short @ close=0.3029 trigger=0.303604"
        )
        rec = _parse_fire_line(line)
        assert rec is not None
        assert rec.symbol == "SKYAIUSDT"
        assert rec.side == "short"
        # KST 11:00 → UTC 02:00 (같은 날)
        assert rec.ts.isoformat() == "2026-05-23T02:00:33+00:00"

    def test_non_fire_line(self):
        assert _parse_fire_line("") is None
        assert _parse_fire_line("random text") is None
        assert _parse_fire_line(
            "2026-05-23 02:00:00,000 INFO airborne_alert_daemon — initial universe"
        ) is None

    def test_malformed_timestamp(self):
        line = (
            "9999-99-99 99:99:99,000 INFO airborne_alert_daemon — "
            "FIRE BTCUSDT long @ close=1 trigger=1"
        )
        assert _parse_fire_line(line) is None


class TestFireRecord:
    def test_key_is_dedup_safe(self):
        ts = datetime(2026, 5, 27, 7, 0, tzinfo=timezone.utc)  # KST 16:00
        r1 = FireRecord(ts, "BTCUSDT", "long", 100.0, 99.5)
        r2 = FireRecord(ts, "BTCUSDT", "long", 100.0, 99.5)
        assert r1.key() == r2.key()
        # 다른 side 는 다른 key
        r3 = FireRecord(ts, "BTCUSDT", "short", 100.0, 99.5)
        assert r1.key() != r3.key()

    def test_kst_hour(self):
        # UTC 07:00 = KST 16:00
        ts = datetime(2026, 5, 27, 7, 0, tzinfo=timezone.utc)
        rec = FireRecord(ts, "BTCUSDT", "long", 100.0, 99.5)
        assert rec.kst_hour() == 16


# ── AirborneFireListener — lifecycle ────────────────────────────────────────
class TestListenerLifecycle:
    def test_requires_start_at(self):
        listener = AirborneFireListener()
        assert not listener.started
        with pytest.raises(RuntimeError, match="start_at"):
            listener.poll_new()

    def test_start_at_rejects_naive(self):
        listener = AirborneFireListener()
        with pytest.raises(ValueError, match="tz-aware"):
            listener.start_at(datetime(2026, 5, 27, 10, 0))

    def test_invalid_window_raises(self):
        with pytest.raises(ValueError):
            AirborneFireListener(dedup_window_hours=0)
        with pytest.raises(ValueError):
            AirborneFireListener(dedup_window_hours=-1)
        with pytest.raises(ValueError):
            AirborneFireListener(poll_lookback_minutes=-1)


# ── AirborneFireListener — polling + dedup ──────────────────────────────────
_FAKE_LOGS_AT_16KST = (
    "2026-05-27 16:00:33,320 INFO airborne_alert_daemon — "
    "FIRE XAGUSDT long @ close=75.24 trigger=74.982\n"
    "2026-05-27 16:00:34,356 INFO airborne_alert_daemon — "
    "FIRE FILUSDT short @ close=1.052 trigger=1.0592\n"
    "2026-05-27 16:00:35,394 INFO airborne_alert_daemon — "
    "FIRE BILLUSDT long @ close=0.08038 trigger=0.07967\n"
    "2026-05-27 16:00:36,000 INFO airborne_alert_daemon — "
    "random non-FIRE line\n"
)


def _listener_with_fixture(now_utc: datetime, logs: str) -> AirborneFireListener:
    """now_utc 시각의 listener + logs fixture 주입."""
    listener = AirborneFireListener()
    listener.start_at(now_utc - timedelta(hours=1))
    # monkeypatch hooks
    listener._read_logs = lambda since_iso: logs
    listener._now_utc = lambda: now_utc
    return listener


class TestListenerPolling:
    def test_returns_parsed_fires(self):
        # KST 16:00 → UTC 07:00
        now = datetime(2026, 5, 27, 7, 30, tzinfo=timezone.utc)
        listener = _listener_with_fixture(now, _FAKE_LOGS_AT_16KST)
        fires = listener.poll_new()
        assert len(fires) == 3
        assert {f.symbol for f in fires} == {"XAGUSDT", "FILUSDT", "BILLUSDT"}
        # 모두 KST 16시
        assert all(f.kst_hour() == 16 for f in fires)

    def test_dedup_on_second_poll(self):
        now = datetime(2026, 5, 27, 7, 30, tzinfo=timezone.utc)
        listener = _listener_with_fixture(now, _FAKE_LOGS_AT_16KST)
        first = listener.poll_new()
        second = listener.poll_new()
        assert len(first) == 3
        assert len(second) == 0
        assert listener.processed_count() == 3

    def test_new_fire_after_dedup(self):
        now = datetime(2026, 5, 27, 7, 30, tzinfo=timezone.utc)
        listener = _listener_with_fixture(now, _FAKE_LOGS_AT_16KST)
        listener.poll_new()
        # 새 fire 추가된 logs
        extra = _FAKE_LOGS_AT_16KST + (
            "2026-05-27 17:00:33,000 INFO airborne_alert_daemon — "
            "FIRE ETHUSDT long @ close=3500 trigger=3490\n"
        )
        listener._read_logs = lambda since_iso: extra
        second = listener.poll_new()
        assert len(second) == 1
        assert second[0].symbol == "ETHUSDT"
        # daemon log 의 시각은 KST 그대로 → KST 17:00 = UTC 08:00, kst_hour=17
        assert second[0].kst_hour() == 17

    def test_ignores_fires_before_start_at(self):
        # start_at = UTC 07:00 / fixture 의 fire 들은 UTC 07:00 (KST 16:00)
        start = datetime(2026, 5, 27, 7, 0, tzinfo=timezone.utc)
        # 다 start_at 시각 이후 (07:00:33 등)
        listener = AirborneFireListener()
        listener.start_at(start)
        listener._read_logs = lambda since_iso: _FAKE_LOGS_AT_16KST
        listener._now_utc = lambda: start + timedelta(minutes=30)
        fires = listener.poll_new()
        assert len(fires) == 3
        # start_at 을 더 미래로
        listener2 = AirborneFireListener()
        listener2.start_at(start + timedelta(hours=2))
        listener2._read_logs = lambda since_iso: _FAKE_LOGS_AT_16KST
        listener2._now_utc = lambda: start + timedelta(hours=2, minutes=30)
        assert listener2.poll_new() == []

    def test_dedup_window_prune(self):
        now = datetime(2026, 5, 27, 7, 30, tzinfo=timezone.utc)
        listener = AirborneFireListener(dedup_window_hours=1.0)
        listener.start_at(now - timedelta(hours=2))
        listener._read_logs = lambda since_iso: _FAKE_LOGS_AT_16KST
        listener._now_utc = lambda: now
        listener.poll_new()
        assert listener.processed_count() == 3
        # 2시간 후 — dedup window 지난 후, 같은 logs 다시 와도 새로 처리
        future = now + timedelta(hours=2)
        listener._now_utc = lambda: future
        fires_again = listener.poll_new()
        # prune 되어서 다시 emit
        assert len(fires_again) == 3


# ── _read_docker_logs — subprocess error handling ──────────────────────────
class TestReadDockerLogs:
    def test_returns_empty_on_filenotfound(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("docker missing")):
            assert _read_docker_logs("any", "2026-01-01T00:00:00Z") == ""

    def test_returns_empty_on_timeout(self):
        import subprocess
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("docker", 15),
        ):
            assert _read_docker_logs("any", "2026-01-01T00:00:00Z") == ""

    def test_returns_empty_on_nonzero_exit(self):
        import subprocess
        mock = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="No such container",
        )
        with patch("subprocess.run", return_value=mock):
            assert _read_docker_logs("any", "2026-01-01T00:00:00Z") == ""

    def test_combines_stdout_and_stderr(self):
        import subprocess
        mock = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="line1\nline2\n", stderr="line3\n",
        )
        with patch("subprocess.run", return_value=mock):
            out = _read_docker_logs("any", "2026-01-01T00:00:00Z")
            assert "line1" in out
            assert "line3" in out

    def test_handles_none_stdout(self):
        """encoding 실패 등으로 stdout 이 None 인 경로 graceful."""
        import subprocess
        mock = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=None, stderr=None,
        )
        with patch("subprocess.run", return_value=mock):
            assert _read_docker_logs("any", "2026-01-01T00:00:00Z") == ""

    def test_since_iso_has_z_suffix(self):
        """daemon-local TZ 사고 방지 — Z (UTC) 명시 필수."""
        # poll_new 가 만드는 since_iso 형식 검증
        listener = AirborneFireListener()
        listener.start_at(datetime(2026, 5, 27, 7, 0, tzinfo=timezone.utc))
        captured: list[str] = []

        def capture(since_iso: str) -> str:
            captured.append(since_iso)
            return ""

        listener._read_logs = capture
        listener._now_utc = lambda: datetime(2026, 5, 27, 7, 30, tzinfo=timezone.utc)
        listener.poll_new()
        assert len(captured) == 1
        assert captured[0].endswith("Z"), (
            f"since_iso 는 Z suffix 필수 (PR #323 fix), got {captured[0]!r}"
        )

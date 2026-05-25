"""Unit tests for airborne FIRE 알림 파싱 (#journal-airborne-fires).

dashboard 의 ``_parse_airborne_fire_line`` + ``_parse_airborne_fires_from_docker_logs``
가 qta-airborne-daemon 의 stdout 라인을 정확히 파싱하는지 검증.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from src.dashboard.app import (
    _parse_airborne_fire_line,
    _parse_airborne_fires_from_docker_logs,
)


class TestParseAirborneFireLine:
    def test_long_fire_parsed(self):
        line = (
            "2026-05-23 02:00:33,327 INFO airborne_alert_daemon — "
            "FIRE CBRSUSDT long @ close=264.52 trigger=263.156"
        )
        rec = _parse_airborne_fire_line(line)
        assert rec is not None
        assert rec["symbol"] == "CBRSUSDT"
        assert rec["side"] == "long"
        assert rec["fire_close"] == 264.52
        assert rec["trigger"] == 263.156
        assert rec["ts"].startswith("2026-05-23T02:00:33")
        assert rec["ts"].endswith("+00:00")

    def test_short_fire_parsed(self):
        line = (
            "2026-05-23 11:00:33,057 INFO airborne_alert_daemon — "
            "FIRE SKYAIUSDT short @ close=0.3029 trigger=0.303604"
        )
        rec = _parse_airborne_fire_line(line)
        assert rec is not None
        assert rec["symbol"] == "SKYAIUSDT"
        assert rec["side"] == "short"
        assert rec["fire_close"] == 0.3029

    def test_non_fire_line_returns_none(self):
        assert _parse_airborne_fire_line(
            "2026-05-23 02:00:00,000 INFO airborne_alert_daemon — initial universe"
        ) is None
        assert _parse_airborne_fire_line("") is None
        assert _parse_airborne_fire_line("random nonsense") is None

    def test_malformed_timestamp_returns_none(self):
        # 정규식은 매칭하지만 strptime 이 실패하는 케이스 — graceful skip
        line = (
            "9999-99-99 99:99:99,000 INFO airborne_alert_daemon — "
            "FIRE BTCUSDT long @ close=1 trigger=1"
        )
        # 정규식 자체가 \d{2} 만 보므로 99:99:99 매칭은 됨 → datetime.strptime 가 실패
        rec = _parse_airborne_fire_line(line)
        assert rec is None


class TestParseAirborneFiresFromDockerLogs:
    def test_returns_empty_when_docker_cli_missing(self):
        """docker CLI 없는 환경 — graceful 빈 리스트."""
        with patch("subprocess.run", side_effect=FileNotFoundError("docker not found")):
            assert _parse_airborne_fires_from_docker_logs("2026-05-26T00:00:00") == []

    def test_returns_empty_when_docker_nonzero_exit(self):
        """컨테이너 미가동 → docker logs 가 non-zero exit. 빈 리스트."""
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="No such container",
        )
        with patch("subprocess.run", return_value=mock_result):
            assert _parse_airborne_fires_from_docker_logs("2026-05-26T00:00:00") == []

    def test_returns_empty_when_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 15)):
            assert _parse_airborne_fires_from_docker_logs("2026-05-26T00:00:00") == []

    def test_parses_multiple_fires_from_stdout(self):
        stdout = (
            "2026-05-23 02:00:33,327 INFO airborne_alert_daemon — "
            "FIRE CBRSUSDT long @ close=264.52 trigger=263.156\n"
            "2026-05-23 11:00:33,057 INFO airborne_alert_daemon — "
            "FIRE SKYAIUSDT short @ close=0.3029 trigger=0.303604\n"
            "2026-05-23 11:00:34,243 INFO airborne_alert_daemon — "
            "universe refresh complete\n"  # non-FIRE 라인, skip
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            fires = _parse_airborne_fires_from_docker_logs("2026-05-23T00:00:00")
        assert len(fires) == 2
        assert fires[0]["symbol"] == "CBRSUSDT"
        assert fires[0]["side"] == "long"
        assert fires[1]["symbol"] == "SKYAIUSDT"
        assert fires[1]["side"] == "short"

    def test_handles_none_stdout_stderr(self):
        """encoding 실패 등으로 stdout/stderr 가 None 인 경로 — crash 안 함."""
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=None, stderr=None,
        )
        with patch("subprocess.run", return_value=mock_result):
            assert _parse_airborne_fires_from_docker_logs("2026-05-26T00:00:00") == []

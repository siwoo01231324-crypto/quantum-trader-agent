"""Unit tests for airborne FIRE 알림 파싱 (#journal-airborne-fires).

dashboard 의 ``_parse_airborne_fire_line`` + ``_parse_airborne_fires_from_docker_logs``
가 qta-airborne-daemon 의 stdout 라인을 정확히 파싱하는지 검증.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from src.dashboard.app import (
    _aggregate_airborne_sims,
    _parse_airborne_fire_line,
    _parse_airborne_fires_from_docker_logs,
    _simulate_airborne_fire,
    AIRBORNE_FEE_PCT,
    AIRBORNE_SL_PCT,
    AIRBORNE_TP_PCT,
)


def _bar(open_=100, high=100, low=100, close=100):
    return {"open": open_, "high": high, "low": low, "close": close,
            "open_time": 0, "close_time": 0}


def _fire(side="long", price=100.0, ts="2026-05-26T02:00:33+00:00",
          symbol="BTCUSDT"):
    return {"ts": ts, "symbol": symbol, "side": side, "fire_close": price,
            "trigger": price}


class TestParseAirborneFireLine:
    def test_long_fire_parsed(self):
        # daemon 컨테이너는 TZ=Asia/Seoul 이라 "02:00:33" 은 KST 로컬.
        # 파서는 KST 로 인식 후 UTC ISO 로 정규화 → KST 02:00 = UTC 전날 17:00.
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
        # KST 2026-05-23 02:00:33 → UTC 2026-05-22 17:00:33
        assert rec["ts"].startswith("2026-05-22T17:00:33")
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
        # KST 2026-05-23 11:00:33 → UTC 2026-05-23 02:00:33
        assert rec["ts"].startswith("2026-05-23T02:00:33")
        assert rec["ts"].endswith("+00:00")

    def test_scientific_notation_price_parsed(self):
        """초저가 코인(LUNC 등)의 과학적표기 가격도 파싱돼야 한다.

        회귀: close=([\\d.]+) 는 6.896e-05 의 'e-' 를 못 잡아 FIRE 통째로 누락
        → 데몬-게이트가 거래 못 시킴 + 대시보드 PF 왜곡 (2026-06-08 LUNCUSDT).
        """
        line = (
            "2026-06-08 13:00:33,327 INFO airborne_alert_daemon — "
            "FIRE LUNCUSDT short @ close=6.896e-05 trigger=6.8978e-05"
        )
        rec = _parse_airborne_fire_line(line)
        assert rec is not None
        assert rec["symbol"] == "LUNCUSDT"
        assert rec["fire_close"] == 6.896e-05
        assert rec["trigger"] == 6.8978e-05

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


class TestSimulateAirborneFire:
    """TP +1.0% / SL -0.5% / 4봉 hold / SL_first 보수적 규칙 검증."""

    def test_long_tp_hits_first_bar(self):
        # entry 100 → high 101.5 (>= TP 101.0) → TP fire bar1
        out = _simulate_airborne_fire(_fire("long", 100.0), [_bar(high=101.5, low=100, close=101.2)])
        assert out["outcome"] == "TP"
        assert out["pct"] == AIRBORNE_TP_PCT * 100
        assert out["bar_idx"] == 1

    def test_long_sl_hits_first_bar(self):
        out = _simulate_airborne_fire(_fire("long", 100.0), [_bar(high=100, low=99.4, close=99.8)])
        assert out["outcome"] == "SL"
        assert out["pct"] == -AIRBORNE_SL_PCT * 100

    def test_long_sl_first_when_same_bar_both_hit(self):
        # high 101.5 (TP) + low 99.4 (SL) 동시 → 보수적 SL 우선
        out = _simulate_airborne_fire(_fire("long", 100.0), [_bar(high=101.5, low=99.4, close=100)])
        assert out["outcome"] == "SL_first"
        assert out["pct"] == -AIRBORNE_SL_PCT * 100

    def test_short_inverts_direction(self):
        # short entry 100 → low 99.0 (<= TP 99.0) → TP
        out = _simulate_airborne_fire(_fire("short", 100.0), [_bar(high=100.4, low=99.0, close=99.3)])
        assert out["outcome"] == "TP"

    def test_timeout_uses_last_close(self):
        # 4봉 모두 TP/SL 미도달 — 마지막 close 로 청산
        bars = [_bar(high=100.5, low=99.7, close=100.3),
                _bar(high=100.6, low=99.8, close=100.2),
                _bar(high=100.7, low=99.9, close=100.4),
                _bar(high=100.8, low=99.8, close=100.4)]
        out = _simulate_airborne_fire(_fire("long", 100.0), bars)
        assert out["outcome"] == "timeout"
        assert out["bar_idx"] == 4
        # +0.4% gross (TP threshold 1% 미달)
        assert out["pct"] == pytest.approx(0.4, abs=0.01)

    def test_empty_bars_returns_none(self):
        assert _simulate_airborne_fire(_fire(), []) is None


class TestAggregateAirborneSims:
    def test_empty_returns_zero_stats(self):
        agg = _aggregate_airborne_sims([])
        assert agg["n"] == 0
        assert agg["win_rate"] is None
        assert agg["pf"] is None
        assert agg["by_side"] == {}
        assert agg["by_kst_bucket"] == []

    def test_mixed_outcomes_aggregate_correctly(self):
        sims = [
            {"ts": "2026-05-26T02:00:33+00:00", "symbol": "X", "side": "long",
             "outcome": "TP", "pct": AIRBORNE_TP_PCT * 100},  # KST 11 = 06-12 오전
            {"ts": "2026-05-26T03:00:33+00:00", "symbol": "X", "side": "short",
             "outcome": "SL", "pct": -AIRBORNE_SL_PCT * 100},  # KST 12 = 12-18 오후
            {"ts": "2026-05-25T18:00:33+00:00", "symbol": "X", "side": "long",
             "outcome": "TP", "pct": AIRBORNE_TP_PCT * 100},  # KST 03 = 00-06 새벽
        ]
        agg = _aggregate_airborne_sims(sims)
        assert agg["n"] == 3
        assert agg["tp"] == 2
        assert agg["sl"] == 1
        assert agg["win_rate"] == pytest.approx(2/3, abs=0.01)
        # sum = 1.0 + 1.0 - 0.5 = 1.5
        assert agg["sum_pct"] == pytest.approx(1.5, abs=0.001)
        # net = sum - fee × n = 1.5 - 0.08 × 3 = 1.26
        assert agg["net_pct"] == pytest.approx(1.5 - AIRBORNE_FEE_PCT * 3, abs=0.001)
        # PF = 2.0 / 0.5 = 4.0
        assert agg["pf"] == pytest.approx(4.0, abs=0.01)

    def test_by_kst_bucket_groups_correctly(self):
        sims = [
            {"ts": "2026-05-25T18:00:33+00:00", "symbol": "X", "side": "long",
             "outcome": "TP", "pct": 1.0},  # KST 03
            {"ts": "2026-05-25T19:00:33+00:00", "symbol": "X", "side": "long",
             "outcome": "SL", "pct": -0.5},  # KST 04
        ]
        agg = _aggregate_airborne_sims(sims)
        assert len(agg["by_kst_bucket"]) == 1
        b = agg["by_kst_bucket"][0]
        assert b["bucket"] == "00-06 새벽"
        assert b["n"] == 2
        assert b["tp"] == 1
        assert b["sl"] == 1

    def test_by_side_groups_correctly(self):
        sims = [
            {"ts": "2026-05-26T02:00:33+00:00", "symbol": "X", "side": "long",
             "outcome": "TP", "pct": 1.0},
            {"ts": "2026-05-26T02:00:33+00:00", "symbol": "X", "side": "short",
             "outcome": "SL", "pct": -0.5},
        ]
        agg = _aggregate_airborne_sims(sims)
        assert "long" in agg["by_side"]
        assert "short" in agg["by_side"]
        assert agg["by_side"]["long"]["n"] == 1
        assert agg["by_side"]["long"]["tp"] == 1
        assert agg["by_side"]["short"]["sl"] == 1

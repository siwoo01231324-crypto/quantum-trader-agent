"""Unit tests for ma-cross (골든/데드 크로스) 파싱 + 시뮬 + 집계.

dashboard 의 ``_parse_ma_cross_line`` + ``_parse_ma_cross_from_docker_logs`` +
``_simulate_ma_cross`` + ``_aggregate_ma_cross_sims`` 가 qta-ma-cross-daemon 의
stdout 라인을 정확히 파싱·시뮬·집계하는지 검증. airborne 테스트 미러.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from src.dashboard.app import (
    MA_CROSS_FEE_PCT,
    MA_CROSS_SL_PCT,
    MA_CROSS_TP_PCT,
    _aggregate_ma_cross_sims,
    _ma_cross_confluence_reject,
    _parse_ma_cross_from_docker_logs,
    _parse_ma_cross_line,
    _simulate_ma_cross,
)


def _bar(open_=100, high=100, low=100, close=100):
    return {"open": open_, "high": high, "low": low, "close": close,
            "open_time": 0, "close_time": 0}


def _cross(cross="golden", price=100.0, ts="2026-06-18T10:00:33+00:00",
           symbol="BTCUSDT"):
    return {"ts": ts, "symbol": symbol, "cross": cross, "close": price,
            "sma_fast": price, "sma_slow": price}


class TestParseMaCrossLine:
    def test_golden_cross_parsed(self):
        # daemon 컨테이너는 TZ=Asia/Seoul → "19:00:33" 은 KST 로컬.
        line = (
            "2026-06-18 19:00:33,565 INFO ma_cross_alert_daemon — "
            "CROSS BTCUSDT golden @ close=67000 sma25=66900 sma200=65000"
        )
        rec = _parse_ma_cross_line(line)
        assert rec is not None
        assert rec["symbol"] == "BTCUSDT"
        assert rec["cross"] == "golden"
        assert rec["close"] == 67000.0
        assert rec["sma_fast"] == 66900.0
        assert rec["sma_slow"] == 65000.0
        # KST 2026-06-18 19:00:33 → UTC 2026-06-18 10:00:33
        assert rec["ts"].startswith("2026-06-18T10:00:33")
        assert rec["ts"].endswith("+00:00")

    def test_death_cross_parsed(self):
        line = (
            "2026-06-18 11:00:33,057 INFO ma_cross_alert_daemon — "
            "CROSS SKYAIUSDT death @ close=0.3029 sma25=0.3030 sma200=0.3050"
        )
        rec = _parse_ma_cross_line(line)
        assert rec is not None
        assert rec["symbol"] == "SKYAIUSDT"
        assert rec["cross"] == "death"
        assert rec["close"] == 0.3029
        # KST 2026-06-18 11:00:33 → UTC 2026-06-18 02:00:33
        assert rec["ts"].startswith("2026-06-18T02:00:33")

    def test_scientific_notation_price_parsed(self):
        """초저가 코인(LUNC 등) 의 과학적표기 가격도 파싱돼야 한다."""
        line = (
            "2026-06-18 13:00:33,327 INFO ma_cross_alert_daemon — "
            "CROSS LUNCUSDT death @ close=6.896e-05 sma25=6.9e-05 sma200=7.1e-05"
        )
        rec = _parse_ma_cross_line(line)
        assert rec is not None
        assert rec["symbol"] == "LUNCUSDT"
        assert rec["close"] == 6.896e-05
        assert rec["sma_fast"] == 6.9e-05

    def test_non_cross_line_returns_none(self):
        assert _parse_ma_cross_line(
            "2026-06-18 02:00:00,000 INFO ma_cross_alert_daemon — initial universe"
        ) is None
        assert _parse_ma_cross_line("") is None
        assert _parse_ma_cross_line("random nonsense") is None
        # airborne FIRE 라인은 ma-cross 파서로 매칭 안 돼야 함
        assert _parse_ma_cross_line(
            "2026-05-23 02:00:33,327 INFO airborne_alert_daemon — "
            "FIRE CBRSUSDT long @ close=264.52 trigger=263.156"
        ) is None

    def test_malformed_timestamp_returns_none(self):
        line = (
            "9999-99-99 99:99:99,000 INFO ma_cross_alert_daemon — "
            "CROSS BTCUSDT golden @ close=1 sma25=1 sma200=1"
        )
        assert _parse_ma_cross_line(line) is None


class TestParseMaCrossFromDockerLogs:
    def test_returns_empty_when_docker_cli_missing(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("docker not found")):
            assert _parse_ma_cross_from_docker_logs("2026-06-18T00:00:00") == []

    def test_returns_empty_when_docker_nonzero_exit(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="No such container",
        )
        with patch("subprocess.run", return_value=mock_result):
            assert _parse_ma_cross_from_docker_logs("2026-06-18T00:00:00") == []

    def test_returns_empty_when_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 15)):
            assert _parse_ma_cross_from_docker_logs("2026-06-18T00:00:00") == []

    def test_parses_multiple_crosses_from_stdout(self):
        stdout = (
            "2026-06-18 19:00:33,565 INFO ma_cross_alert_daemon — "
            "CROSS BTCUSDT golden @ close=67000 sma25=66900 sma200=65000\n"
            "2026-06-18 11:00:33,057 INFO ma_cross_alert_daemon — "
            "CROSS SKYAIUSDT death @ close=0.3029 sma25=0.3030 sma200=0.3050\n"
            "2026-06-18 11:00:34,243 INFO ma_cross_alert_daemon — "
            "universe refresh complete\n"  # non-CROSS, skip
        )
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            crosses = _parse_ma_cross_from_docker_logs("2026-06-18T00:00:00")
        assert len(crosses) == 2
        assert crosses[0]["symbol"] == "BTCUSDT"
        assert crosses[0]["cross"] == "golden"
        assert crosses[1]["symbol"] == "SKYAIUSDT"
        assert crosses[1]["cross"] == "death"

    def test_handles_none_stdout_stderr(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=None, stderr=None,
        )
        with patch("subprocess.run", return_value=mock_result):
            assert _parse_ma_cross_from_docker_logs("2026-06-18T00:00:00") == []


class TestSimulateMaCross:
    """golden=롱 / death=숏 / TP +12% / SL -2% / SL_first 보수 규칙 검증."""

    def test_golden_tp_hits_first_bar(self):
        # entry 100 → high 113 (>= TP 112) → TP bar1
        out = _simulate_ma_cross(_cross("golden", 100.0),
                                 [_bar(high=113, low=100, close=112.5)])
        assert out["outcome"] == "TP"
        assert out["pct"] == MA_CROSS_TP_PCT * 100
        assert out["bar_idx"] == 1

    def test_golden_sl_hits_first_bar(self):
        # entry 100 → low 97.5 (<= SL 98) → SL
        out = _simulate_ma_cross(_cross("golden", 100.0),
                                 [_bar(high=100.5, low=97.5, close=98.5)])
        assert out["outcome"] == "SL"
        assert out["pct"] == -MA_CROSS_SL_PCT * 100

    def test_golden_sl_first_when_same_bar_both_hit(self):
        # high 113 (TP) + low 97.5 (SL) 동시 → 보수적 SL 우선
        out = _simulate_ma_cross(_cross("golden", 100.0),
                                 [_bar(high=113, low=97.5, close=100)])
        assert out["outcome"] == "SL_first"
        assert out["pct"] == -MA_CROSS_SL_PCT * 100

    def test_death_inverts_direction(self):
        # death(숏) entry 100 → low 88 (<= TP 88) → TP
        out = _simulate_ma_cross(_cross("death", 100.0),
                                 [_bar(high=100.5, low=88, close=89)])
        assert out["outcome"] == "TP"
        assert out["pct"] == MA_CROSS_TP_PCT * 100

    def test_death_sl_when_price_rises(self):
        # death(숏) entry 100 → high 102.5 (>= SL 102) → SL
        out = _simulate_ma_cross(_cross("death", 100.0),
                                 [_bar(high=102.5, low=99, close=102)])
        assert out["outcome"] == "SL"

    def test_timeout_uses_last_close(self):
        # hold_bars=2 로 축소 — 2봉 모두 TP/SL 미도달 → 마지막 close 청산
        bars = [_bar(high=101, low=99, close=100.5),
                _bar(high=101, low=99, close=100.8)]
        out = _simulate_ma_cross(_cross("golden", 100.0), bars, hold_bars=2)
        assert out["outcome"] == "timeout"
        assert out["bar_idx"] == 2
        assert out["pct"] == pytest.approx(0.8, abs=0.01)

    def test_empty_bars_returns_none(self):
        assert _simulate_ma_cross(_cross(), []) is None

    def test_incomplete_returns_none(self):
        """hold_bars 미만 + TP/SL 조기 종결도 없으면 None (캐시 오염 방지)."""
        # 1봉만, TP/SL 미도달, hold_bars=720 기본 → incomplete
        out = _simulate_ma_cross(_cross("golden", 100.0),
                                 [_bar(high=100.5, low=99.5, close=100.1)])
        assert out is None

    def test_incomplete_but_early_tp_returns_tp(self):
        """1봉뿐이라도 TP 찍으면 final → 정상 반환 (조기 종결은 final)."""
        out = _simulate_ma_cross(_cross("golden", 100.0),
                                 [_bar(high=113, low=99.9, close=112.5)])
        assert out is not None
        assert out["outcome"] == "TP"


class TestAggregateMaCrossSims:
    def test_empty_returns_zero_stats(self):
        agg = _aggregate_ma_cross_sims([])
        assert agg["n"] == 0
        assert agg["win_rate"] is None
        assert agg["pf"] is None
        assert agg["by_cross"] == {}
        assert agg["by_kst_bucket"] == []

    def test_mixed_outcomes_aggregate_correctly(self):
        sims = [
            {"ts": "2026-06-18T02:00:33+00:00", "symbol": "X", "cross": "golden",
             "outcome": "TP", "pct": MA_CROSS_TP_PCT * 100},
            {"ts": "2026-06-18T03:00:33+00:00", "symbol": "Y", "cross": "death",
             "outcome": "SL", "pct": -MA_CROSS_SL_PCT * 100},
            {"ts": "2026-06-17T18:00:33+00:00", "symbol": "Z", "cross": "golden",
             "outcome": "TP", "pct": MA_CROSS_TP_PCT * 100},
        ]
        agg = _aggregate_ma_cross_sims(sims)
        assert agg["n"] == 3
        assert agg["tp"] == 2
        assert agg["sl"] == 1
        assert agg["win_rate"] == pytest.approx(2/3, abs=0.01)
        # sum = 12 + 12 - 2 = 22
        assert agg["sum_pct"] == pytest.approx(22.0, abs=0.001)
        assert agg["net_pct"] == pytest.approx(22.0 - MA_CROSS_FEE_PCT * 3, abs=0.001)
        # PF = 24 / 2 = 12
        assert agg["pf"] == pytest.approx(12.0, abs=0.01)

    def test_by_cross_groups_correctly(self):
        sims = [
            {"ts": "2026-06-18T02:00:33+00:00", "symbol": "X", "cross": "golden",
             "outcome": "TP", "pct": 12.0},
            {"ts": "2026-06-18T02:00:33+00:00", "symbol": "Y", "cross": "death",
             "outcome": "SL", "pct": -2.0},
        ]
        agg = _aggregate_ma_cross_sims(sims)
        assert "golden" in agg["by_cross"]
        assert "death" in agg["by_cross"]
        assert agg["by_cross"]["golden"]["n"] == 1
        assert agg["by_cross"]["golden"]["tp"] == 1
        assert agg["by_cross"]["death"]["sl"] == 1

    def test_by_kst_bucket_groups_correctly(self):
        sims = [
            {"ts": "2026-06-17T18:00:33+00:00", "symbol": "X", "cross": "golden",
             "outcome": "TP", "pct": 12.0},  # KST 03
            {"ts": "2026-06-17T19:00:33+00:00", "symbol": "Y", "cross": "death",
             "outcome": "SL", "pct": -2.0},  # KST 04
        ]
        agg = _aggregate_ma_cross_sims(sims)
        assert len(agg["by_kst_bucket"]) == 1
        b = agg["by_kst_bucket"][0]
        assert b["bucket"] == "00-06 새벽"
        assert b["n"] == 2
        assert b["tp"] == 1
        assert b["sl"] == 1


class TestConfluenceReject:
    """confluence 필터(_ma_cross_confluence_reject) — 숏-집중 대시보드 근사."""

    # KST 3시 = 18:00 UTC (게이트 안). death+down+close<sma+ext작음 → 통과(None).
    _IN = {"cross": "death", "btc_regime": "down", "close": 105.0,
           "sma_slow": 109.0, "ts": "2026-06-18T18:00:00+00:00"}

    def test_passes_full_short_confluence(self):
        assert _ma_cross_confluence_reject(self._IN) is None

    def test_long_rejected(self):
        r = _ma_cross_confluence_reject({**self._IN, "cross": "golden"})
        assert r is not None and "long" in r

    def test_btc_up_rejected(self):
        r = _ma_cross_confluence_reject({**self._IN, "btc_regime": "up"})
        assert r is not None and "btc_regime" in r

    def test_kst_offhour_rejected(self):
        # 03:00 UTC = KST 12시 (게이트 밖).
        r = _ma_cross_confluence_reject({**self._IN, "ts": "2026-06-18T03:00:00+00:00"})
        assert r is not None and "kst_hour" in r

    def test_self_sma200_above_rejected(self):
        # 숏인데 close > sma_slow → 자기200 위.
        r = _ma_cross_confluence_reject({**self._IN, "close": 115.0})
        assert r is not None and "self_sma200" in r

    def test_overextended_rejected(self):
        # close 90 vs sma 109 → 약 21% 이탈 (>10%).
        r = _ma_cross_confluence_reject({**self._IN, "close": 90.0})
        assert r is not None and "overextended" in r

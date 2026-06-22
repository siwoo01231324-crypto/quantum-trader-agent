"""fetch_position_history_pnl 집계 로직 단위 테스트 (API 는 mock).

거래소 청산이력 → 일일손익(auto_pnl_ledger) 집계. routine 이 읽는 단일 진실
이므로 PF/승패/총손익 계산과 graceful 실패를 박제한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from scripts import bitget_account_reconcile as mod  # noqa: E402


def _fake_position(symbol, side, net, ctime, utime):
    return {
        "symbol": symbol, "holdSide": side, "netProfit": str(net),
        "ctime": str(ctime), "utime": str(utime),
    }


def _patch(monkeypatch, *, creds=("k", "s", "p"), resp=None):
    monkeypatch.setattr(mod, "_autoload_env", lambda: None)
    monkeypatch.setattr(mod, "_creds", lambda: creds)
    if resp is not None:
        monkeypatch.setattr(mod, "_signed_get", lambda *a, **k: resp)


def test_aggregates_pnl_wins_losses_pf(monkeypatch):
    # 2승(+4, +1) / 2패(-2, -1) → net +2, gross 5/3, PF 1.6667
    resp = {"code": "00000", "data": {"list": [
        _fake_position("AAA", "short", 4.0, 1, 2),
        _fake_position("BBB", "long", -2.0, 3, 4),
        _fake_position("CCC", "short", 1.0, 5, 6),
        _fake_position("DDD", "short", -1.0, 7, 8),
    ]}}
    _patch(monkeypatch, resp=resp)
    r = mod.fetch_position_history_pnl("2026-06-21")
    assert r["ok"] is True
    assert r["total_net"] == 2.0
    assert r["wins"] == 2 and r["losses"] == 2
    assert r["gross_win"] == 5.0 and r["gross_loss"] == 3.0
    assert r["profit_factor"] == pytest.approx(1.6667, abs=1e-3)
    assert r["n_positions"] == 4
    assert r["source"] == "bitget-exchange-history-position"


def test_positions_sorted_by_open_time(monkeypatch):
    resp = {"code": "00000", "data": {"list": [
        _fake_position("LATE", "short", 1.0, 9000, 9100),
        _fake_position("EARLY", "short", 1.0, 1000, 1100),
    ]}}
    _patch(monkeypatch, resp=resp)
    r = mod.fetch_position_history_pnl("2026-06-21")
    assert [p["symbol"] for p in r["positions"]] == ["EARLY", "LATE"]


def test_all_wins_pf_is_none(monkeypatch):
    """gross_loss 0 이면 PF None (0 division 방지)."""
    resp = {"code": "00000", "data": {"list": [
        _fake_position("AAA", "short", 2.0, 1, 2),
    ]}}
    _patch(monkeypatch, resp=resp)
    r = mod.fetch_position_history_pnl("2026-06-21")
    assert r["profit_factor"] is None
    assert r["total_net"] == 2.0


def test_empty_list_ok_zero(monkeypatch):
    _patch(monkeypatch, resp={"code": "00000", "data": {"list": []}})
    r = mod.fetch_position_history_pnl("2026-06-21")
    assert r["ok"] is True
    assert r["n_positions"] == 0 and r["total_net"] == 0


def test_no_creds_graceful(monkeypatch):
    _patch(monkeypatch, creds=None)
    r = mod.fetch_position_history_pnl("2026-06-21")
    assert r["ok"] is False
    assert "자격증명" in r["error"]


def test_api_error_code_graceful(monkeypatch):
    _patch(monkeypatch, resp={"code": "40037", "msg": "bad", "data": {}})
    r = mod.fetch_position_history_pnl("2026-06-21")
    assert r["ok"] is False
    assert "40037" in r["error"]


def test_signed_get_exception_graceful(monkeypatch):
    monkeypatch.setattr(mod, "_autoload_env", lambda: None)
    monkeypatch.setattr(mod, "_creds", lambda: ("k", "s", "p"))

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(mod, "_signed_get", _boom)
    r = mod.fetch_position_history_pnl("2026-06-21")
    assert r["ok"] is False
    assert "network down" in r["error"]

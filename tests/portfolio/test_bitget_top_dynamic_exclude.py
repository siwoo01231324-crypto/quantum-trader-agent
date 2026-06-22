"""ORDIUSDT(사용자 수동 보유) 봇 유니버스 제외 검증 — 2026-06-22.

ORDIUSDT 는 사용자가 수동 보유·관리하는 포지션. 같은 Bitget 계좌에서 봇이
진입하면 네팅 충돌 → 진입 차단. 라이브 두 진입 게이트(strategy.get_universe·
fire_consumer.spec.universe)가 모두 get_top_n_symbols 를 경유하므로, 이 한
chokepoint 에서 제외하면 봇이 ORDIUSDT 를 영영 안 건드린다.
"""
from __future__ import annotations

import src.portfolio.bitget_top_dynamic as bt


def test_is_excluded_ordiusdt():
    assert bt._is_excluded("ORDIUSDT") is True
    # 일반 종목은 통과
    assert bt._is_excluded("BTCUSDT") is False
    assert bt._is_excluded("SUIUSDT") is False


def test_get_top_n_filters_ordiusdt(monkeypatch):
    """티커 API 가 ORDIUSDT 를 (거래량 1위로) 줘도 결과에서 빠진다."""
    class _FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"code": "00000", "data": [
                {"symbol": "ORDIUSDT", "usdtVolume": "9e12"},   # 1위로 넣어도
                {"symbol": "BTCUSDT", "usdtVolume": "8e12"},
                {"symbol": "ETHUSDT", "usdtVolume": "7e12"},
                {"symbol": "SUIUSDT", "usdtVolume": "6e12"},
            ]}

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k): return _FakeResp()

    monkeypatch.setattr(bt.httpx, "Client", _FakeClient)
    bt.clear_cache()
    out = bt.get_top_n_symbols(3)
    assert "ORDIUSDT" not in out, "ORDIUSDT 가 유니버스에 들어옴 — 봇이 진입할 수 있음"
    assert out == ["BTCUSDT", "ETHUSDT", "SUIUSDT"]
    bt.clear_cache()


def test_fallback_universe_excludes_ordiusdt(monkeypatch):
    """정적 fallback 에도 제외 필터 적용 (ORDIUSDT 가 목록에 섞여도 빠짐)."""
    monkeypatch.setattr(bt, "BITGET_USDT_TOP30",
                        ["BTCUSDT", "ORDIUSDT", "ETHUSDT"])
    assert "ORDIUSDT" not in bt._fallback_universe()
    assert bt._fallback_universe() == ["BTCUSDT", "ETHUSDT"]

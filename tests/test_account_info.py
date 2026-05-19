"""AccountInfoProvider + /api/account/info endpoint 테스트 (#182)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.dashboard.account_info import AccountInfoProvider, _safe_int
from src.dashboard.app import DashboardState, create_app


# ---------------------------------------------------------------------------
# _safe_int
# ---------------------------------------------------------------------------

class TestSafeInt:
    def test_str_int(self) -> None:
        assert _safe_int("100000") == 100000

    def test_str_float(self) -> None:
        assert _safe_int("1234.56") == 1234

    def test_none(self) -> None:
        assert _safe_int(None) == 0

    def test_invalid(self) -> None:
        assert _safe_int("abc") == 0


# ---------------------------------------------------------------------------
# AccountInfoProvider
# ---------------------------------------------------------------------------

class TestAccountInfoProvider:
    def test_missing_envs_returns_errors_for_both(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for k in ("HANTOO_FAKE_API_KEY", "HANTOO_FAKE_SECRET_API_KEY",
                  "HANTOO_FAKE_CREDIT_NUMBER", "HANTOO_CREDIT_NUMBER",
                  "KIS_APP_KEY", "KIS_APP_SECRET",
                  "BINANCE_API_KEY", "BINANCE_API_SECRET", "BINANCE_SECRET_KEY",
                  "BINANCE_DEMO_API_KEY", "BINANCE_DEMO__SECRET_API_KEY",
                  "BINANCE_DEMO_SECRET_API_KEY",
                  "BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"):
            monkeypatch.delenv(k, raising=False)
        provider = AccountInfoProvider()
        result = provider.fetch()
        assert "kis" in result and "binance" in result
        assert result["kis"]["ok"] is False
        assert result["binance"]["ok"] is False

    def test_caching(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 캐시 TTL 안에 두 번 호출 → 내부 fetch 한 번만 호출
        provider = AccountInfoProvider(ttl_sec=10.0)
        calls = {"kis": 0, "binance": 0}

        def _kis(self) -> dict:
            calls["kis"] += 1
            return {"ok": True, "cano_masked": "1234****-01"}

        def _bnb(self) -> dict:
            calls["binance"] += 1
            return {"ok": True, "api_key_masked": "ab****cd"}

        monkeypatch.setattr(AccountInfoProvider, "_fetch_kis", _kis)
        monkeypatch.setattr(AccountInfoProvider, "_fetch_binance", _bnb)
        provider.fetch()
        provider.fetch()
        assert calls["kis"] == 1
        assert calls["binance"] == 1


# ---------------------------------------------------------------------------
# AccountInfoProvider.fetch_binance — Binance-only fast path (10s 폴링용).
# KIS REST 한도를 건드리지 않고 Binance 미실현손익만 자주 갱신.
# ---------------------------------------------------------------------------

class TestFetchBinanceOnly:
    def test_returns_binance_only_with_ts_and_no_kis_call(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        provider = AccountInfoProvider()
        calls = {"kis": 0, "binance": 0}

        def _kis(self) -> dict:
            calls["kis"] += 1
            return {"ok": True}

        def _bnb(self) -> dict:
            calls["binance"] += 1
            return {"ok": True, "api_key_masked": "ab****cd",
                    "total_unrealized_pnl": -1.23}

        monkeypatch.setattr(AccountInfoProvider, "_fetch_kis", _kis)
        monkeypatch.setattr(AccountInfoProvider, "_fetch_binance", _bnb)
        out = provider.fetch_binance()
        assert out["ok"] is True
        assert out["total_unrealized_pnl"] == -1.23
        # 스냅샷 시각 — 클라이언트가 "n초 전" 표시에 사용.
        assert isinstance(out.get("ts"), str) and out["ts"]
        # 핵심: KIS REST 는 절대 호출 안 됨 (10s 폴링이 KIS 한도 안 건드림).
        assert calls["kis"] == 0
        assert calls["binance"] == 1

    def test_binance_only_single_flight_cached_within_ttl(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        provider = AccountInfoProvider()
        n = {"binance": 0}

        def _bnb(self) -> dict:
            n["binance"] += 1
            return {"ok": True}

        monkeypatch.setattr(AccountInfoProvider, "_fetch_binance", _bnb)
        a = provider.fetch_binance()
        b = provider.fetch_binance()
        assert n["binance"] == 1            # TTL 내 단일 fetch
        assert a["ts"] == b["ts"]           # 캐시 반환은 원본 스냅샷 시각 유지


# ---------------------------------------------------------------------------
# /api/account/binance endpoint — Binance 전용 (10s 폴링)
# ---------------------------------------------------------------------------

class TestAccountBinanceEndpoint:
    def test_unavailable_without_provider(self) -> None:
        state = DashboardState()
        state.account_info_provider = None
        client = TestClient(create_app(state))
        resp = client.get("/api/account/binance")
        assert resp.status_code == 200
        assert resp.json() == {"available": False}

    def test_returns_binance_payload(self) -> None:
        class _Stub:
            def fetch_binance(self) -> dict:
                return {"ok": True, "wallet_balance_usdt": 1000.0,
                        "total_unrealized_pnl": -2.5, "positions": [],
                        "ts": "2026-05-19T07:30:20+00:00"}

        state = DashboardState()
        state.account_info_provider = _Stub()
        client = TestClient(create_app(state))
        resp = client.get("/api/account/binance")
        assert resp.status_code == 200
        d = resp.json()
        assert d["available"] is True
        assert d["binance"]["ok"] is True
        assert d["binance"]["total_unrealized_pnl"] == -2.5
        assert d["binance"]["ts"] == "2026-05-19T07:30:20+00:00"

    def test_falls_back_to_fetch_when_provider_lacks_fetch_binance(self) -> None:
        # 구형 provider(.fetch 만 보유) 도 500 없이 동작해야.
        class _Old:
            def fetch(self) -> dict:
                return {"kis": {"ok": False},
                        "binance": {"ok": True, "wallet_balance_usdt": 9.0}}

        state = DashboardState()
        state.account_info_provider = _Old()
        client = TestClient(create_app(state))
        resp = client.get("/api/account/binance")
        assert resp.status_code == 200
        d = resp.json()
        assert d["available"] is True
        assert d["binance"]["ok"] is True
        assert d["binance"]["wallet_balance_usdt"] == 9.0


# ---------------------------------------------------------------------------
# /api/account/info endpoint
# ---------------------------------------------------------------------------

class TestAccountInfoEndpoint:
    def test_returns_unavailable_without_provider(self) -> None:
        state = DashboardState()
        state.account_info_provider = None
        client = TestClient(create_app(state))
        resp = client.get("/api/account/info")
        assert resp.status_code == 200
        assert resp.json() == {"available": False}

    def test_returns_provider_data(self) -> None:
        class _Stub:
            def fetch(self) -> dict:
                return {
                    "kis": {
                        "ok": True, "paper": True, "cano_masked": "1234****-01",
                        "cash_balance": 100000000, "eval_amount": 100000000,
                        "n_positions": 0,
                    },
                    "binance": {
                        "ok": True, "testnet": True, "api_key_masked": "OQ2H****EnK",
                        "wallet_balance_usdt": 1000.0, "available_usdt": 1000.0,
                        "base_url_short": "testnet.binancefuture.com",
                    },
                }

        state = DashboardState()
        state.account_info_provider = _Stub()
        client = TestClient(create_app(state))
        resp = client.get("/api/account/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["kis"]["ok"] is True
        assert data["kis"]["cano_masked"] == "1234****-01"
        assert data["binance"]["ok"] is True
        assert data["binance"]["wallet_balance_usdt"] == 1000.0


# ---------------------------------------------------------------------------
# HTML — 내 계좌 카드 존재
# ---------------------------------------------------------------------------

class TestAccountCard:
    def test_root_contains_kis_and_binance_cards(self) -> None:
        client = TestClient(create_app(DashboardState()))
        body = client.get("/").text
        # KIS 카드
        assert "KIS 계좌" in body
        assert "kis-cano" in body
        # Binance 카드
        assert "Binance Futures" in body
        assert "bnb-wallet" in body
        # JS 폴링
        assert "/api/account/info" in body or "acctRefresh" in body

    def test_binance_card_has_fast_poll_and_snapshot_element(self) -> None:
        body = TestClient(create_app(DashboardState())).get("/").text
        # Binance 전용 10s 폴링 + 조회시각 표시 요소
        assert "/api/account/binance" in body
        assert "bnb-snap" in body

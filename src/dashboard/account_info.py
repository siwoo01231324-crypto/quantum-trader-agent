"""KIS + Binance 계좌 인증·잔고 조회 — dashboard "내 계좌" 카드용 (#182).

EXE 더블클릭 후 "진짜 내 계좌 맞나" 확인이 가능하도록 두 거래소 모두
계좌 식별자(마스킹) + 잔고 + 인증 상태를 표시한다.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BINANCE_BASE_URL_TESTNET = "https://testnet.binancefuture.com"
DEFAULT_BINANCE_BASE_URL_LIVE = "https://fapi.binance.com"


class AccountInfoProvider:
    """KIS + Binance 잔고를 5초 TTL 캐싱.

    fetch() 는 {"kis": {...}, "binance": {...}} 형태로 두 거래소 결과를 함께 반환.
    각 dict 의 ok=True/False 로 인증 상태 분기. 실패해도 다른 거래소는 정상 표시.
    """

    def __init__(self, ttl_sec: float = 5.0) -> None:
        self._cache: dict[str, Any] | None = None
        self._cache_at: datetime | None = None
        self._ttl = ttl_sec

    def fetch(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        if (
            self._cache is not None
            and self._cache_at is not None
            and (now - self._cache_at).total_seconds() < self._ttl
        ):
            return self._cache

        kis = self._safe(self._fetch_kis, "KIS")
        binance = self._safe(self._fetch_binance, "Binance")
        data = {"kis": kis, "binance": binance}
        self._cache = data
        self._cache_at = now
        return data

    @staticmethod
    def _safe(callback, label: str) -> dict[str, Any]:
        try:
            return callback()
        except Exception as err:  # noqa: BLE001
            logger.warning("%s account fetch failed: %s", label, err)
            return {"ok": False, "error": f"{type(err).__name__}: {err}"}

    # ── KIS (한국투자증권 paper) ─────────────────────────────────────────────

    def _fetch_kis(self) -> dict[str, Any]:
        app_key = os.environ.get("HANTOO_FAKE_API_KEY") or os.environ.get("KIS_APP_KEY")
        app_secret = (
            os.environ.get("HANTOO_FAKE_SECRET_API_KEY")
            or os.environ.get("KIS_APP_SECRET")
        )
        credit = (
            os.environ.get("HANTOO_FAKE_CREDIT_NUMBER")
            or os.environ.get("HANTOO_CREDIT_NUMBER", "")
        )
        if not app_key or not app_secret or not credit:
            return {
                "ok": False,
                "error": "KIS 자격증명 누락 (HANTOO_FAKE_API_KEY / SECRET / CREDIT_NUMBER)",
            }

        cano = credit[:8] if len(credit) >= 8 else credit
        acnt_prdt_cd = credit[9:11] if len(credit) >= 11 and credit[8] == "-" else "01"

        from src.brokers.kis.auth import KISAuth  # noqa: PLC0415
        from src.brokers.kis.rest import KISClient  # noqa: PLC0415

        auth = KISAuth(app_key=app_key, app_secret=app_secret, paper=True)
        client = KISClient(
            auth=auth,
            app_key=app_key,
            app_secret=app_secret,
            cano=cano,
            acnt_prdt_cd=acnt_prdt_cd,
            paper=True,
        )
        bal = client.get_balance()

        out2 = (bal.output2 or [{}])[0]
        cash = _safe_int(out2.get("DNCA_TOT_AMT") or out2.get("dnca_tot_amt"))
        eval_amt = _safe_int(out2.get("TOT_EVLU_AMT") or out2.get("tot_evlu_amt"))
        masked = (cano[:4] + "****" + "-" + acnt_prdt_cd) if len(cano) >= 4 else cano

        return {
            "ok": True,
            "paper": True,
            "cano_masked": masked,
            "cash_balance": cash,
            "eval_amount": eval_amt,
            "n_positions": len(bal.output1 or []),
            "rt_cd": getattr(bal, "rt_cd", None),
        }

    # ── Binance USDS-M Futures ───────────────────────────────────────────────

    def _fetch_binance(self) -> dict[str, Any]:
        # default testnet=true (paper 운영 안전). BINANCE_TESTNET=false 시 mainnet.
        testnet = (os.environ.get("BINANCE_TESTNET", "true").lower() == "true")

        def _strip(v: str | None) -> str:
            return (v or "").strip().strip('"').strip("'")

        if testnet:
            # testnet/demo 키 우선. 없으면 mainnet 키로 fallback (값은 그대로 testnet endpoint).
            api_key = _strip(
                os.environ.get("BINANCE_DEMO_API_KEY")
                or os.environ.get("BINANCE_TESTNET_API_KEY")
                or os.environ.get("BINANCE_API_KEY")
            )
            api_secret = _strip(
                os.environ.get("BINANCE_DEMO__SECRET_API_KEY")
                or os.environ.get("BINANCE_DEMO_SECRET_API_KEY")
                or os.environ.get("BINANCE_TESTNET_API_SECRET")
                or os.environ.get("BINANCE_API_SECRET")
                or os.environ.get("BINANCE_SECRET_KEY")
            )
        else:
            api_key = _strip(os.environ.get("BINANCE_API_KEY"))
            api_secret = _strip(
                os.environ.get("BINANCE_API_SECRET")
                or os.environ.get("BINANCE_SECRET_KEY")
            )

        if not api_key or not api_secret:
            return {
                "ok": False,
                "error": "Binance 자격증명 누락 (testnet=true 시 BINANCE_DEMO_API_KEY 등 / mainnet 시 BINANCE_API_KEY)",
            }

        base_url = (
            os.environ.get("BINANCE_BASE_URL")
            or (DEFAULT_BINANCE_BASE_URL_TESTNET if testnet else DEFAULT_BINANCE_BASE_URL_LIVE)
        )

        from src.brokers.binance.rest import BinanceFuturesClient  # noqa: PLC0415
        from src.brokers.rate_limiter import RateLimiter  # noqa: PLC0415

        client = BinanceFuturesClient(
            api_key=api_key,
            secret=api_secret,
            base_url=base_url,
            rate_limiter=RateLimiter(),
        )
        balances = client.get_balance()
        # USDT 가 잔고 단위. balance 항목의 .asset / .balance / .availableBalance 사용
        usdt = next((b for b in balances if getattr(b, "asset", None) == "USDT"), None)
        wallet = float(getattr(usdt, "balance", 0)) if usdt else 0.0
        available = float(getattr(usdt, "available_balance", 0) or getattr(usdt, "availableBalance", 0) or 0) if usdt else 0.0

        api_key_masked = api_key[:4] + "****" + api_key[-4:] if len(api_key) >= 8 else api_key
        return {
            "ok": True,
            "testnet": testnet,
            "base_url_short": base_url.replace("https://", ""),
            "api_key_masked": api_key_masked,
            "wallet_balance_usdt": round(wallet, 4),
            "available_usdt": round(available, 4),
        }


def _safe_int(value: Any) -> int:
    try:
        return int(float(value)) if value is not None else 0
    except (TypeError, ValueError):
        return 0

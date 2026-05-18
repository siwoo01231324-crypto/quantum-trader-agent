"""KIS + Binance 계좌 인증·잔고 조회 — dashboard "내 계좌" 카드용 (#182).

EXE 더블클릭 후 "진짜 내 계좌 맞나" 확인이 가능하도록 두 거래소 모두
계좌 식별자(마스킹) + 잔고 + 인증 상태를 표시한다.
"""
from __future__ import annotations

import logging
import os
import threading
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

    def __init__(self, ttl_sec: float = 15.0) -> None:
        # #231 — TTL 5s → 15s. JS polling 5s 와 동기화돼 매 polling 마다 실 API
        # 호출 → KIS EGW00201 rate-limit / 가변 latency 가 ✓/✗ 토글로 사용자에게
        # "조회중↔정보" 깜박임 보이던 패턴 fix. 15s 면 polling 3회 중 1회만 실 fetch.
        self._cache: dict[str, Any] | None = None
        self._cache_at: datetime | None = None
        self._ttl = ttl_sec
        # #2 (prior-review MEDIUM) — the 15s TTL check-then-act below is now
        # hit concurrently by the dashboard /api/account/info worker thread
        # (asyncio.to_thread(provider.fetch)) AND the live pipeline via
        # SnapshotBuilder._inject_real_equity (shared instance on the
        # attached/smoke path after #18). Unguarded it stampedes the slow
        # KIS+Binance REST and can return a torn/partial dict.
        #
        # Strategy: SINGLE-FLIGHT with two locks, neither held across the
        # cache-hit fast path.
        #   - `_state_lock` (short): guards the cache read/write tuple so the
        #     TTL check + store is atomic and never returns a torn dict.
        #   - `_refresh_lock` (single-flight): only ONE thread runs the slow
        #     KIS+Binance REST on a miss; concurrent callers block on this
        #     lock, then re-read the now-fresh cache and reuse the in-flight
        #     result instead of re-fetching (at most one underlying refresh).
        # A warm-cache read takes only the short `_state_lock` and returns —
        # it never touches `_refresh_lock`, so concurrent reads are NOT
        # serialized behind a slow REST for >TTL. Both locks are
        # non-reentrant and fetch() never calls itself → no deadlock.
        self._state_lock = threading.Lock()
        self._refresh_lock = threading.Lock()

    def _read_fresh_cache(self) -> dict[str, Any] | None:
        """Return the cached dict iff still within TTL, else None (atomic)."""
        with self._state_lock:
            if (
                self._cache is not None
                and self._cache_at is not None
                and (datetime.now(timezone.utc) - self._cache_at).total_seconds()
                < self._ttl
            ):
                return self._cache
            return None

    def peek(self) -> dict[str, Any] | None:
        """Non-blocking cached read — returns the last fetched balances dict
        without ever touching the network (None if nothing cached yet,
        regardless of TTL).

        #3 (prior-review MEDIUM): the live consumer coroutine runs the sync
        `build_snapshot` on the event-loop thread. `_inject_real_equity`
        reads balances via this peek() so the loop thread NEVER blocks on a
        cache-miss REST. The actual refresh runs off-loop via fetch()
        (`SnapshotBuilder.refresh_balance` wrapped in asyncio.to_thread).
        Returns last-known balances even past TTL so a transient refresh
        failure does not blank the snapshot (last-known-good upstream).
        """
        with self._state_lock:
            return self._cache

    def fetch(self) -> dict[str, Any]:
        # Fast path: a warm cache hit takes only the short state lock.
        cached = self._read_fresh_cache()
        if cached is not None:
            return cached

        # Miss → single-flight: only one thread runs the slow REST. Late
        # arrivals block here, then re-check the cache the winner just stored.
        with self._refresh_lock:
            cached = self._read_fresh_cache()
            if cached is not None:
                return cached  # winner already refreshed → reuse in-flight result

            # Snapshot the prior cache for the per-broker fallback under the
            # state lock so we never read self._cache (mutable) racily.
            with self._state_lock:
                prev_cache = self._cache

            # Per-broker fallback (#231) — 한쪽 거래소 fetch 실패 시 이전
            # cache 의 그 거래소 응답을 재사용. ok=False 로 덮어쓰던 패턴 →
            # "잠깐 정보 → 조회중↔에러" 깜박임 방지.
            kis = self._safe(self._fetch_kis, "KIS")
            if not kis.get("ok") and prev_cache is not None:
                prev_kis = prev_cache.get("kis", {})
                if prev_kis.get("ok"):
                    logger.debug("KIS fetch failed — reusing previous cache value")
                    kis = prev_kis
            binance = self._safe(self._fetch_binance, "Binance")
            if not binance.get("ok") and prev_cache is not None:
                prev_bn = prev_cache.get("binance", {})
                if prev_bn.get("ok"):
                    logger.debug(
                        "Binance fetch failed — reusing previous cache value"
                    )
                    binance = prev_bn

            data = {"kis": kis, "binance": binance}
            with self._state_lock:
                self._cache = data
                self._cache_at = datetime.now(timezone.utc)
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

        # #238 — 실제 broker 열린 포지션 + 미실현손익. 대시보드가 잔고만
        # 보여주고 실제 포지션(폭주 잔재 포함)을 안 띄우던 문제 fix. WAL 집계
        # "전략별 포지션"은 전략 *의도*, 이건 broker *실제 상태* (account truth).
        positions: list[dict] = []
        total_unrealized = 0.0
        try:
            for r in client.get_position_risk():
                amt = float(r.positionAmt)
                if amt == 0:
                    continue
                upnl = float(r.unRealizedProfit)
                total_unrealized += upnl
                positions.append({
                    "symbol": r.symbol,
                    "amt": amt,
                    "side": "LONG" if amt > 0 else "SHORT",
                    "entry_price": float(r.entryPrice),
                    "mark_price": float(r.markPrice),
                    "unrealized_pnl": round(upnl, 4),
                })
        except Exception as err:  # noqa: BLE001
            logger.warning("Binance position_risk fetch failed: %s", err)

        api_key_masked = api_key[:4] + "****" + api_key[-4:] if len(api_key) >= 8 else api_key
        return {
            "ok": True,
            "testnet": testnet,
            "base_url_short": base_url.replace("https://", ""),
            "api_key_masked": api_key_masked,
            "wallet_balance_usdt": round(wallet, 4),
            "available_usdt": round(available, 4),
            "total_unrealized_pnl": round(total_unrealized, 4),
            "positions": positions,
            "n_positions": len(positions),
        }


def _safe_int(value: Any) -> int:
    try:
        return int(float(value)) if value is not None else 0
    except (TypeError, ValueError):
        return 0

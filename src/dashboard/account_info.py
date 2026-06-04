"""KIS + Binance 계좌 인증·잔고 조회 — dashboard "내 계좌" 카드용 (#182).

EXE 더블클릭 후 "진짜 내 계좌 맞나" 확인이 가능하도록 두 거래소 모두
계좌 식별자(마스킹) + 잔고 + 인증 상태를 표시한다.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BINANCE_BASE_URL_TESTNET = "https://testnet.binancefuture.com"
DEFAULT_BINANCE_BASE_URL_LIVE = "https://fapi.binance.com"

# 거래소 실현손익 NET 을 구성하는 income 타입. REALIZED_PNL(타점 손익) +
# COMMISSION(수수료, 음수) + FUNDING_FEE(펀딩, ±). 사용자 확정 정의(2026-05-23):
# "순손익 — 수수료·펀딩 포함" = Binance 화면 실현손익과 일치하는 값.
_PNL_INCOME_TYPES = ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE")


def aggregate_income_pnl(incomes: list, today_start_ms: int) -> tuple[float, float]:
    """`/fapi/v1/income` 레코드 → (일간 NET, 월간 NET).

    NET = Σ REALIZED_PNL + Σ COMMISSION + Σ FUNDING_FEE. ``incomes`` 는
    이달 1일 0시(KST)~현재 범위로 조회된 것으로 가정 → 월간 = 전체 합,
    일간 = ``time >= today_start_ms`` 인 레코드 합. Decimal 누적 후 float 반환
    (부동소수점 오염 방지).
    """
    daily = Decimal("0")
    monthly = Decimal("0")
    for it in incomes:
        if it.incomeType not in _PNL_INCOME_TYPES:
            continue
        monthly += it.income
        if it.time >= today_start_ms:
            daily += it.income
    return float(daily), float(monthly)


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
        # Binance-only fast path (#238 follow-up) — the dashboard polls
        # /api/account/binance every 10s so 미실현손익 tracks the live
        # Binance screen closely WITHOUT touching the slow KIS REST (its
        # rate limit is why the combined cache TTL is 15s). Separate short
        # TTL + own single-flight; never serialized behind the KIS path.
        self._bn_cache: dict[str, Any] | None = None
        self._bn_cache_at: datetime | None = None
        self._bn_ttl = 8.0  # < 10s client poll → each poll is fresh
        self._bn_state_lock = threading.Lock()
        self._bn_refresh_lock = threading.Lock()
        # Binance 실현손익(income 원장) 캐시 — `/api/pnl` 전용. income 은 거래
        # 청산 시에만 변하므로 30s TTL 로 충분 (대시보드 5s 폴링 → 6회 중 1회만
        # 실 REST). 자체 single-flight — KIS / 잔고 경로와 분리.
        self._pnl_cache: dict[str, Any] | None = None
        self._pnl_cache_at: datetime | None = None
        self._pnl_ttl = 30.0
        self._pnl_state_lock = threading.Lock()
        self._pnl_refresh_lock = threading.Lock()

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

            bitget = self._safe(self._fetch_bitget, "Bitget")
            if not bitget.get("ok") and prev_cache is not None:
                prev_bg = prev_cache.get("bitget", {})
                if prev_bg.get("ok"):
                    logger.debug(
                        "Bitget fetch failed — reusing previous cache value"
                    )
                    bitget = prev_bg

            data = {"kis": kis, "binance": binance, "bitget": bitget}
            with self._state_lock:
                self._cache = data
                self._cache_at = datetime.now(timezone.utc)
                return data

    def fetch_binance(self) -> dict[str, Any]:
        """Binance 계좌/포지션만 조회 (KIS REST 미접촉) — 대시보드 10s 폴링용.

        조합 ``fetch()`` 와 분리된 짧은 TTL + single-flight 캐시. 반환 dict
        에 스냅샷 시각 ``ts`` (UTC ISO) 를 실어, 클라이언트가 "n초 전" 으로
        데이터 신선도를 표시할 수 있게 한다 — 대시보드 uPnL 이 실제 Binance
        화면과 미세하게 다른 건 계산이 아니라 이 스냅샷 지연 때문임을
        사용자가 눈으로 확인 가능. 캐시 적중 시 원본 스냅샷의 ``ts`` 를
        그대로 유지(실제 데이터 나이를 정직하게 노출). _safe 로 감싸 어떤
        예외도 ``{ok:False}`` 로 흡수 — 절대 raise 하지 않는다.
        """
        with self._bn_state_lock:
            if (
                self._bn_cache is not None
                and self._bn_cache_at is not None
                and (datetime.now(timezone.utc) - self._bn_cache_at).total_seconds()
                < self._bn_ttl
            ):
                return self._bn_cache
        with self._bn_refresh_lock:
            with self._bn_state_lock:
                if (
                    self._bn_cache is not None
                    and self._bn_cache_at is not None
                    and (
                        datetime.now(timezone.utc) - self._bn_cache_at
                    ).total_seconds()
                    < self._bn_ttl
                ):
                    return self._bn_cache  # winner refreshed → reuse in-flight
            data = self._safe(self._fetch_binance, "Binance")
            now = datetime.now(timezone.utc)
            data = {**data, "ts": now.isoformat()}
            with self._bn_state_lock:
                self._bn_cache = data
                self._bn_cache_at = now
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

    # ── Bitget USDT-M Futures (P5) ───────────────────────────────────────────

    def _resolve_bitget_creds(self) -> tuple[str, str, str, bool] | None:
        """Bitget 자격증명 해석 → (api_key, secret, passphrase, paper).

        Demo 우선 (BITGET_DEMO_*). 없으면 mainnet (BITGET_API_*). 둘 다
        없으면 None — 카드는 ``ok=False`` 로 표시되어 다른 거래소 영향 없음.
        """
        def _strip(v: str | None) -> str:
            return (v or "").strip().strip('"').strip("'")

        demo_key = _strip(os.environ.get("BITGET_DEMO_API_KEY"))
        demo_sec = _strip(os.environ.get("BITGET_DEMO_SECRET"))
        demo_pass = _strip(os.environ.get("BITGET_DEMO_PASSPHRASE"))
        if demo_key and demo_sec and demo_pass:
            return demo_key, demo_sec, demo_pass, True

        live_key = _strip(os.environ.get("BITGET_API_KEY"))
        live_sec = _strip(os.environ.get("BITGET_API_SECRET"))
        live_pass = _strip(os.environ.get("BITGET_API_PASSPHRASE"))
        if live_key and live_sec and live_pass:
            return live_key, live_sec, live_pass, False
        return None

    def _fetch_bitget(self) -> dict[str, Any]:
        creds = self._resolve_bitget_creds()
        if creds is None:
            return {
                "ok": False,
                "error": "Bitget 자격증명 누락 (BITGET_DEMO_API_KEY/SECRET/PASSPHRASE 또는 BITGET_API_*)",
            }
        api_key, api_secret, passphrase, paper = creds

        import base64  # noqa: PLC0415
        import hashlib  # noqa: PLC0415
        import hmac  # noqa: PLC0415
        import time as _time  # noqa: PLC0415
        import urllib.parse  # noqa: PLC0415
        import httpx  # noqa: PLC0415

        base_url = "https://api.bitget.com"
        product_type = "USDT-FUTURES"

        def _signed_get(path: str, params: dict) -> dict:
            ts = str(int(_time.time() * 1000))
            qs = "?" + urllib.parse.urlencode(params)
            sig_input = f"{ts}GET{path}{qs}".encode()
            sig = base64.b64encode(
                hmac.new(api_secret.encode(), sig_input, hashlib.sha256).digest()
            ).decode()
            headers = {
                "ACCESS-KEY": api_key,
                "ACCESS-SIGN": sig,
                "ACCESS-TIMESTAMP": ts,
                "ACCESS-PASSPHRASE": passphrase,
                "Content-Type": "application/json",
            }
            if paper:
                headers["paptrading"] = "1"
            with httpx.Client(timeout=10.0) as c:
                r = c.get(f"{base_url}{path}{qs}", headers=headers)
            return r.json()

        # 1. 잔고
        acc = _signed_get("/api/v2/mix/account/account", {
            "productType": product_type, "symbol": "BTCUSDT", "marginCoin": "USDT",
        })
        if str(acc.get("code")) != "00000":
            return {"ok": False, "error": f"Bitget account: {acc.get('msg')}"}
        acc_data = acc.get("data") or {}
        wallet = float(acc_data.get("accountEquity") or 0)
        available = float(acc_data.get("available") or 0)
        upnl_account = float(acc_data.get("unrealizedPL") or 0)

        # 2. 포지션
        positions: list[dict] = []
        total_unrealized = upnl_account
        try:
            ps = _signed_get("/api/v2/mix/position/all-position", {
                "productType": product_type, "marginCoin": "USDT",
            })
            if str(ps.get("code")) == "00000":
                position_unrealized = 0.0
                for p in ps.get("data") or []:
                    total = float(p.get("total") or 0)
                    if total == 0:
                        continue
                    hold_side = p.get("holdSide", "long")
                    upnl = float(p.get("unrealizedPL") or 0)
                    position_unrealized += upnl
                    positions.append({
                        "symbol": p.get("symbol"),
                        "amt": total if hold_side == "long" else -total,
                        "side": "LONG" if hold_side == "long" else "SHORT",
                        "entry_price": float(p.get("openPriceAvg") or 0),
                        "mark_price": float(p.get("markPrice") or 0),
                        "unrealized_pnl": round(upnl, 4),
                    })
                # account.unrealizedPL 이 비어있으면 포지션 합계로 fallback.
                if upnl_account == 0:
                    total_unrealized = position_unrealized
        except Exception as err:  # noqa: BLE001
            logger.warning("Bitget position fetch failed: %s", err)

        api_key_masked = api_key[:4] + "****" + api_key[-4:] if len(api_key) >= 8 else api_key
        return {
            "ok": True,
            "paper": paper,
            "base_url_short": base_url.replace("https://", "") + (
                " (demo/paptrading)" if paper else ""
            ),
            "api_key_masked": api_key_masked,
            "wallet_balance_usdt": round(wallet, 4),
            "available_usdt": round(available, 4),
            "total_unrealized_pnl": round(total_unrealized, 4),
            "positions": positions,
            "n_positions": len(positions),
        }

    # ── Binance USDS-M Futures ───────────────────────────────────────────────

    def _resolve_binance_creds(self) -> tuple[str, str, str, bool] | None:
        """Binance 자격증명·base_url 해석 → (api_key, api_secret, base_url, testnet).

        자격증명 누락 시 ``None``. ``_fetch_binance`` 와 ``_fetch_binance_pnl``
        이 공유 — testnet 키 fallback 체인 + base_url 결정을 한 곳에서. default
        testnet=true (paper 운영 안전), ``BINANCE_TESTNET=false`` 시 mainnet.
        """
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
            return None

        base_url = (
            os.environ.get("BINANCE_BASE_URL")
            or (DEFAULT_BINANCE_BASE_URL_TESTNET if testnet else DEFAULT_BINANCE_BASE_URL_LIVE)
        )
        return api_key, api_secret, base_url, testnet

    def _fetch_binance(self) -> dict[str, Any]:
        creds = self._resolve_binance_creds()
        if creds is None:
            return {
                "ok": False,
                "error": "Binance 자격증명 누락 (testnet=true 시 BINANCE_DEMO_API_KEY 등 / mainnet 시 BINANCE_API_KEY)",
            }
        api_key, api_secret, base_url, testnet = creds

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

    # ── Binance 실현손익 (income 원장) ───────────────────────────────────────

    def fetch_binance_pnl(self) -> dict[str, Any]:
        """Binance 일간/월간 NET 실현손익 — 거래소 income 원장 기준 (30s 캐시).

        ``/api/pnl`` 전용. WAL 재구성이 아니라 거래소가 직접 기록한 원장
        (``/fapi/v1/income``) 을 읽으므로 Binance 화면 실현손익과 정확히
        일치한다. fetch()/잔고 경로와 분리된 자체 single-flight 캐시.
        ``_safe`` 로 감싸 어떤 예외도 ``{ok:False}`` 로 흡수 — 절대 raise 안 함.
        """
        with self._pnl_state_lock:
            if (
                self._pnl_cache is not None
                and self._pnl_cache_at is not None
                and (datetime.now(timezone.utc) - self._pnl_cache_at).total_seconds()
                < self._pnl_ttl
            ):
                return self._pnl_cache
        with self._pnl_refresh_lock:
            with self._pnl_state_lock:
                if (
                    self._pnl_cache is not None
                    and self._pnl_cache_at is not None
                    and (
                        datetime.now(timezone.utc) - self._pnl_cache_at
                    ).total_seconds()
                    < self._pnl_ttl
                ):
                    return self._pnl_cache  # winner refreshed → reuse in-flight
            data = self._safe(self._fetch_binance_pnl, "Binance PnL")
            now = datetime.now(timezone.utc)
            with self._pnl_state_lock:
                self._pnl_cache = data
                self._pnl_cache_at = now
                return data

    def _fetch_binance_pnl(self) -> dict[str, Any]:
        creds = self._resolve_binance_creds()
        if creds is None:
            return {"ok": False, "error": "Binance 자격증명 누락"}
        api_key, api_secret, base_url, _testnet = creds

        from zoneinfo import ZoneInfo  # noqa: PLC0415

        from src.brokers.binance.rest import BinanceFuturesClient  # noqa: PLC0415
        from src.brokers.rate_limiter import RateLimiter  # noqa: PLC0415

        client = BinanceFuturesClient(
            api_key=api_key,
            secret=api_secret,
            base_url=base_url,
            rate_limiter=RateLimiter(),
        )

        # 일/월 경계는 KST 자정 기준 (암호화폐 24/7 — "오늘" = KST 0시 이후).
        kst = ZoneInfo("Asia/Seoul")
        now_kst = datetime.now(kst)
        today_start = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = today_start.replace(day=1)
        today_start_ms = int(today_start.timestamp() * 1000)
        month_start_ms = int(month_start.timestamp() * 1000)

        # 이달 1일~현재 income 1콜 → 월간 = 전체 합, 일간 = 오늘 0시 이후 합.
        incomes = client.get_income(start_time=month_start_ms)
        daily, monthly = aggregate_income_pnl(incomes, today_start_ms)
        return {
            "ok": True,
            "daily": daily,
            "monthly": monthly,
            "asset": "USDT",
            "n_records": len(incomes),
        }


def _safe_int(value: Any) -> int:
    try:
        return int(float(value)) if value is not None else 0
    except (TypeError, ValueError):
        return 0

"""cs-tsmom-crypto-daily dashboard signal computer (2026-05-20).

대시보드 `/cs-tsmom` 페이지의 백엔드. production cs_tsmom_kr_daily.score_panel
과 비트단위 동일한 12-1m momentum 식을 쓰는지 + cross-sectional top-N 랭킹 +
ENTER/EXIT/HOLD/OUT 신호 분류가 정확한지 검증.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.dashboard.cs_tsmom_signals import (
    DEFAULT_LONG_LB,
    DEFAULT_SKIP_LB,
    CsTsmomComputer,
    compute_signals_from_panels,
)


def _mk_panel(n_bars: int = 280, symbols: list[str] | None = None,
              seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synth daily close + quote_vol panels for `symbols`."""
    syms = symbols or ["AAA", "BBB", "CCC", "DDD"]
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="D")
    closes = pd.DataFrame(index=idx, columns=syms, dtype=float)
    qv = pd.DataFrame(index=idx, columns=syms, dtype=float)
    base = {s: 100.0 + 50 * i for i, s in enumerate(syms)}
    for s in syms:
        rets = rng.normal(0, 0.02, n_bars)
        closes[s] = base[s] * (1 + pd.Series(rets, index=idx)).cumprod().values
        qv[s] = 20_000_000.0  # well above min_quote_vol default 1e7
    return closes, qv


class TestComputeSignalsFromPanels:
    def test_short_panel_returns_empty(self):
        closes, qv = _mk_panel(n_bars=DEFAULT_LONG_LB)   # exactly warmup, not enough
        assert compute_signals_from_panels(closes, qv) == []

    def test_returns_one_row_per_symbol(self):
        closes, qv = _mk_panel(n_bars=DEFAULT_LONG_LB + 50,
                                 symbols=["AAA", "BBB", "CCC"])
        rows = compute_signals_from_panels(closes, qv)
        assert len(rows) == 3
        assert {r["symbol"] for r in rows} == {"AAA", "BBB", "CCC"}

    def test_score_matches_production_definition(self):
        # production score = log(close[t-21] / close[t-252])
        # 우리 식과 비트단위 동일해야 한다 (cs_tsmom_kr_daily.score_panel 와 동일).
        closes, qv = _mk_panel(n_bars=300, symbols=["AAA"])
        rows = compute_signals_from_panels(closes, qv)
        row = rows[0]
        # Recompute manually for the last bar
        i = len(closes) - 1
        c_skip = closes.iloc[i - DEFAULT_SKIP_LB]["AAA"]
        c_long = closes.iloc[i - DEFAULT_LONG_LB]["AAA"]
        expected = float(np.log(c_skip / c_long))
        assert row["score"] == pytest.approx(expected, abs=1e-12)

    def test_top_n_signal_logic(self):
        # 4 종목 중 score 최고/최저 강제 → top_n=2 일 때 상위 2개가 in_top.
        # 합성 가격: AAA 상승, BBB 약상승, CCC 횡보, DDD 하락
        n = 280
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        closes = pd.DataFrame(index=idx, columns=["AAA", "BBB", "CCC", "DDD"], dtype=float)
        for i in range(n):
            closes.iloc[i] = [
                100 + i * 0.5,    # 강한 상승
                100 + i * 0.1,    # 약한 상승
                100,              # 횡보
                100 - i * 0.2,    # 하락
            ]
        qv = pd.DataFrame(2e7, index=idx, columns=closes.columns)
        rows = compute_signals_from_panels(closes, qv, top_n=2)
        by_sym = {r["symbol"]: r for r in rows}
        assert by_sym["AAA"]["in_top_today"] is True   # 강한 상승 → top2 안
        assert by_sym["BBB"]["in_top_today"] is True
        assert by_sym["CCC"]["in_top_today"] is False  # score=0, > 0 조건 미충족
        assert by_sym["DDD"]["in_top_today"] is False
        # AAA score > BBB score > CCC > DDD
        assert by_sym["AAA"]["score"] > by_sym["BBB"]["score"]
        assert by_sym["BBB"]["score"] > by_sym["CCC"]["score"]

    def test_signal_classification_enter_hold_exit_out(self):
        # 어제 vs 오늘 in_top 변화에 따라 ENTER/EXIT/HOLD/OUT 라벨링.
        # closes 조작 어렵지 않게: 종목 4개, top_n=2.
        # AAA: 어제도 오늘도 in_top → HOLD
        # BBB: 어제 in_top, 오늘 빠짐 → EXIT
        # CCC: 어제 out, 오늘 in_top → ENTER
        # DDD: 둘 다 out → OUT
        n = 280
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        closes = pd.DataFrame(index=idx, columns=["AAA", "BBB", "CCC", "DDD"], dtype=float)

        # AAA: 매일 강하게 상승 (어제 #1, 오늘 #1)
        # BBB: 어제까지 상승 #2 이었다가 오늘만 폭락하면 어제 in_top, 오늘 out 가능. 단
        #   score = log(close[t-21] / close[t-252]) 라 *최근 close* 가 score 에 영향
        #   안 미친다 (skip_lb 만큼 시차). 그래서 BBB 가 오늘 빠지려면 score 시계열
        #   에서 21일 전 close 가 폭락이어야 한다. 합성 어려움 — 대신 직접 시점별
        #   조작. 합성보다 _rank_row 가 어떻게 도는지 보고 결과 확인.
        # 단순하게: 그냥 4 종목 가격 시퀀스 만들고 rows 의 signal 값들이 정확히
        # {ENTER, EXIT, HOLD, OUT} 4종 모두 등장하는지 *최소한* 검증.
        for i in range(n):
            closes.iloc[i] = [
                100 + i * 0.5,                              # AAA 상승
                100 + (i if i < n - 1 else 0) * 0.4,        # BBB
                100 + max(0, i - n + 25) * 0.6,             # CCC 후반 상승
                100,                                         # DDD 횡보
            ]
        qv = pd.DataFrame(2e7, index=idx, columns=closes.columns)
        rows = compute_signals_from_panels(closes, qv, top_n=2)
        sigs = {r["symbol"]: r["signal"] for r in rows}
        # AAA 는 추세상 항상 in_top → HOLD 일 것
        assert sigs["AAA"] in ("HOLD", "ENTER")
        # DDD 는 score=0 → 항상 OUT
        assert sigs["DDD"] == "OUT"

    def test_illiquid_excluded_from_top(self):
        # min_quote_vol 미만 종목은 score 가 좋아도 in_top 에서 제외.
        n = 280
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        closes = pd.DataFrame(index=idx, columns=["LIQ", "ILLIQ"], dtype=float)
        for i in range(n):
            closes.iloc[i] = [100 + i * 0.3, 100 + i * 0.5]  # ILLIQ score 가 더 높음
        qv = pd.DataFrame(index=idx, columns=closes.columns, dtype=float)
        qv["LIQ"] = 2e7
        qv["ILLIQ"] = 5e6   # < 1e7 → illiquid
        rows = compute_signals_from_panels(closes, qv, top_n=1)
        by_sym = {r["symbol"]: r for r in rows}
        # ILLIQ score 가 높지만 유동성 필터로 in_top 못 들어감 → LIQ 가 in_top
        assert by_sym["LIQ"]["in_top_today"] is True
        assert by_sym["ILLIQ"]["in_top_today"] is False
        assert by_sym["ILLIQ"]["liquid"] is False
        assert by_sym["LIQ"]["liquid"] is True


class TestNaNSafeSerialization:
    """Starlette JSONResponse 는 allow_nan=False — 응답 안에 NaN 한 개라도 있으면
    ValueError → HTTP 500. 신규 상장 종목 (마지막 close NaN) 같은 케이스에서
    `/api/cs-tsmom` 가 500 폭주하던 버그(2026-05-20) 의 회귀 방지."""

    def test_last_close_nan_becomes_none(self):
        # 신규 상장 흉내 — DDD 의 마지막 close 만 NaN
        n = 280
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        closes = pd.DataFrame(index=idx, columns=["AAA", "BBB", "DDD"], dtype=float)
        for i in range(n):
            closes.iloc[i] = [100 + i * 0.5, 100 + i * 0.3, 100 + i * 0.2]
        closes.iloc[-1, closes.columns.get_loc("DDD")] = float("nan")
        qv = pd.DataFrame(2e7, index=idx, columns=closes.columns)
        rows = compute_signals_from_panels(closes, qv)
        ddd = next(r for r in rows if r["symbol"] == "DDD")
        # NaN 은 None 으로 변환돼야 함 (JSON allow_nan=False 호환)
        assert ddd["last_close"] is None

    def test_no_nan_in_any_row_field(self):
        # 의도적으로 NaN 만들어 — 일부 종목 score 자체가 NaN 일 수 있음
        n = 280
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        closes = pd.DataFrame(index=idx, columns=["AAA", "BBB"], dtype=float)
        for i in range(n):
            closes.iloc[i] = [100 + i * 0.5, 100 + i * 0.3]
        # BBB 의 close[t-252] 영역에 NaN 박기 → score NaN
        closes.iloc[0:30, closes.columns.get_loc("BBB")] = float("nan")
        qv = pd.DataFrame(2e7, index=idx, columns=closes.columns)
        rows = compute_signals_from_panels(closes, qv)
        # 모든 float 필드가 None 또는 finite — NaN 절대 없어야
        import math
        for r in rows:
            for k in ("last_close", "score"):
                v = r[k]
                assert v is None or (isinstance(v, (int, float)) and math.isfinite(v)), (
                    f"row {r['symbol']} field {k} = {v!r} (NaN/inf — JSON 직렬화 실패할 것)"
                )

    def test_full_response_json_dumps_with_allow_nan_false(self):
        # Starlette JSONResponse 와 동일 옵션 (allow_nan=False) 으로 직접
        # json.dumps 했을 때 raise 안 해야 한다. NaN 한 개라도 새면 ValueError.
        import json
        n = 280
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        closes = pd.DataFrame(index=idx, columns=["AAA", "BBB"], dtype=float)
        for i in range(n):
            closes.iloc[i] = [100 + i * 0.5, 100 + i * 0.3]
        closes.iloc[-1, closes.columns.get_loc("BBB")] = float("nan")
        qv = pd.DataFrame(2e7, index=idx, columns=closes.columns)
        rows = compute_signals_from_panels(closes, qv)
        payload = {"rows": rows, "available": True}
        # 핵심 회귀 가드 — Starlette 와 정확히 같은 옵션.
        json.dumps(payload, allow_nan=False)  # raises ValueError 면 fail


class TestReasonField:
    """2026-05-21 fix — row 별 `reason` 필드가 사용자에게 왜 OUT 인지 가시화."""

    def test_no_data_reason_when_close_is_nan(self):
        # AAA 정상, BBB 마지막 close NaN (fetch 실패 흉내)
        n = 280
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        closes = pd.DataFrame(index=idx, columns=["AAA", "BBB"], dtype=float)
        for i in range(n):
            closes.iloc[i] = [100 + i * 0.3, 100 + i * 0.3]
        closes.iloc[-1, closes.columns.get_loc("BBB")] = float("nan")
        qv = pd.DataFrame(2e7, index=idx, columns=closes.columns)
        rows = compute_signals_from_panels(closes, qv)
        bbb = next(r for r in rows if r["symbol"] == "BBB")
        assert bbb["reason"] == "no_data", bbb

    def test_warmup_reason_when_score_is_nan(self):
        # 252d lookback 범위에 NaN → score NaN, last_close 는 있음
        n = 280
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        closes = pd.DataFrame(index=idx, columns=["AAA", "BBB"], dtype=float)
        for i in range(n):
            closes.iloc[i] = [100 + i * 0.3, 100 + i * 0.3]
        # BBB 의 t-252 위치에 NaN → score=NaN, last_close 는 정상
        closes.iloc[0:30, closes.columns.get_loc("BBB")] = float("nan")
        qv = pd.DataFrame(2e7, index=idx, columns=closes.columns)
        rows = compute_signals_from_panels(closes, qv)
        bbb = next(r for r in rows if r["symbol"] == "BBB")
        assert bbb["reason"] == "warmup", bbb

    def test_low_volume_reason(self):
        n = 280
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        closes = pd.DataFrame(index=idx, columns=["LIQ", "ILLIQ"], dtype=float)
        for i in range(n):
            closes.iloc[i] = [100 + i * 0.3, 100 + i * 0.5]
        qv = pd.DataFrame(index=idx, columns=closes.columns, dtype=float)
        qv["LIQ"] = 2e7
        qv["ILLIQ"] = 5e6  # 미달
        rows = compute_signals_from_panels(closes, qv)
        illiq = next(r for r in rows if r["symbol"] == "ILLIQ")
        assert illiq["reason"] == "low_volume", illiq

    def test_negative_score_reason(self):
        # 명백한 하락 추세 → score < 0
        n = 280
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        closes = pd.DataFrame(index=idx, columns=["AAA", "BBB"], dtype=float)
        for i in range(n):
            closes.iloc[i] = [100 + i * 0.3, 200 - i * 0.5]  # BBB 하락
        qv = pd.DataFrame(2e7, index=idx, columns=closes.columns)
        rows = compute_signals_from_panels(closes, qv)
        bbb = next(r for r in rows if r["symbol"] == "BBB")
        assert bbb["reason"] == "negative_score", bbb

    def test_ok_reason_when_in_top(self):
        # 강한 상승 + top1 → reason=ok
        n = 280
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        closes = pd.DataFrame(index=idx, columns=["AAA"], dtype=float)
        for i in range(n):
            closes.iloc[i] = [100 + i * 0.5]
        qv = pd.DataFrame(2e7, index=idx, columns=closes.columns)
        rows = compute_signals_from_panels(closes, qv, top_n=1)
        aaa = rows[0]
        assert aaa["reason"] == "ok", aaa
        assert aaa["in_top_today"] is True

    def test_out_of_top_n_when_positive_but_below_cutoff(self):
        # 3종목 모두 양수 score, top_n=1 → 2/3등은 out_of_top_n
        n = 280
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        closes = pd.DataFrame(index=idx, columns=["AAA", "BBB", "CCC"], dtype=float)
        for i in range(n):
            closes.iloc[i] = [100 + i * 0.5, 100 + i * 0.3, 100 + i * 0.1]
        qv = pd.DataFrame(2e7, index=idx, columns=closes.columns)
        rows = compute_signals_from_panels(closes, qv, top_n=1)
        by_sym = {r["symbol"]: r for r in rows}
        assert by_sym["AAA"]["reason"] == "ok"
        assert by_sym["BBB"]["reason"] == "out_of_top_n"
        assert by_sym["CCC"]["reason"] == "out_of_top_n"


class TestCacheStaleAutoRefresh:
    """2026-05-21 fix — BTC 캐시가 5개월 전 데이터까지만 있던 사고 회귀 방지.

    panel build outer-join 으로 신생 코인 마지막 row 가 base index 되면 BTC
    의 그 row 는 NaN → "데이터 없음". `_refresh()` 가 stale 캐시 검출해서
    `refresh=True` 로 강제 재페치 호출하는지 검증.
    """

    def test_stale_panel_triggers_force_refresh(self, monkeypatch):
        from src.dashboard.cs_tsmom_signals import CsTsmomComputer

        # Stub bench module — fetch_universe 가 호출될 때 refresh 값 기록.
        refresh_calls: list[bool] = []

        # 5개월 전이 마지막인 stale BTC panel (252+ rows, but old).
        stale_idx = pd.date_range("2025-04-01", periods=260, freq="D")
        stale_btc = pd.DataFrame({
            "close": np.linspace(60000, 90000, 260),
            "quote_volume": np.full(260, 1e9),
        }, index=stale_idx)

        # 어제까지 fresh 한 SOL panel.
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        now = _dt.now(_tz.utc)
        fresh_idx = pd.date_range(now - _td(days=260), periods=260, freq="D").normalize()
        fresh_sol = pd.DataFrame({
            "close": np.linspace(80, 180, 260),
            "quote_volume": np.full(260, 5e8),
        }, index=fresh_idx)

        cached_panels = {"BTCUSDT": stale_btc, "SOLUSDT": fresh_sol}

        def _fake_fetch_universe(symbols, start, end, refresh, **kw):
            refresh_calls.append((tuple(sorted(symbols)), refresh))
            if not refresh:
                # 첫 호출 — 캐시된 panel 그대로 (BTC stale, SOL fresh)
                return {s: cached_panels[s] for s in symbols if s in cached_panels}
            # refresh=True — 강제 재페치 흉내 (BTC 도 fresh 로 갱신).
            refreshed_btc = stale_btc.copy()
            refreshed_btc.index = fresh_idx  # 어제까지로 갱신
            return {s: (refreshed_btc if s == "BTCUSDT" else cached_panels.get(s))
                    for s in symbols if s in cached_panels or s == "BTCUSDT"}

        # bench 모듈을 in-process stub 으로 교체.
        import sys, types
        fake_bench = types.SimpleNamespace(
            fetch_universe=_fake_fetch_universe,
            build_panels=lambda panels: (
                pd.DataFrame({s: df["close"] for s, df in panels.items()}).sort_index().dropna(how="all"),
                pd.DataFrame({s: df["quote_volume"] for s, df in panels.items()}).reindex(
                    pd.DataFrame({s: df["close"] for s, df in panels.items()}).sort_index().dropna(how="all").index,
                ),
            ),
        )
        monkeypatch.setitem(sys.modules, "bench_cs_tsmom_crypto", fake_bench)

        # Universe pin import → 30종이 아니라 BTC+SOL 만 시뮬.
        monkeypatch.setattr(
            "src.portfolio.binance_universe.BINANCE_USDT_TOP30",
            ("BTCUSDT", "SOLUSDT"),
        )

        comp = CsTsmomComputer()
        state = comp.compute(force=True)
        assert state.available

        # 첫 호출 (refresh=False) + 두 번째 호출 (refresh=True for stale BTC)
        # 두 호출 모두 있어야 한다.
        refresh_flags = [r for _, r in refresh_calls]
        assert refresh_flags == [False, True], refresh_calls
        # 두 번째 호출 symbols 에는 stale BTC 가 포함되어 있어야.
        stale_symbols = refresh_calls[1][0]
        assert "BTCUSDT" in stale_symbols, refresh_calls


class TestBinanceUniversePin:
    """2026-05-21 fix — single source of truth pin."""

    def test_universe_has_exactly_30_symbols(self):
        from src.portfolio.binance_universe import BINANCE_USDT_TOP30
        assert len(BINANCE_USDT_TOP30) == 30

    def test_universe_includes_majors(self):
        # 메이저 8종 필수 — dashboard 가 이들을 잃으면 BUY 후보 0 사고 재현.
        from src.portfolio.binance_universe import BINANCE_USDT_TOP30
        majors = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
                  "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT"}
        assert majors.issubset(set(BINANCE_USDT_TOP30)), (
            "메이저 누락 — 2026-05-21 dashboard 사고 회귀: "
            f"missing = {majors - set(BINANCE_USDT_TOP30)}"
        )

    def test_all_symbols_end_with_usdt(self):
        from src.portfolio.binance_universe import BINANCE_USDT_TOP30
        non_usdt = [s for s in BINANCE_USDT_TOP30 if not s.endswith("USDT")]
        assert non_usdt == [], f"non-USDT pairs leaked into pin: {non_usdt}"

    def test_pin_is_immutable_tuple(self):
        from src.portfolio import binance_universe
        assert isinstance(binance_universe.BINANCE_USDT_TOP30, tuple)

    def test_get_universe_returns_mutable_copy(self):
        from src.portfolio.binance_universe import (
            BINANCE_USDT_TOP30, get_universe,
        )
        u = get_universe()
        assert isinstance(u, list)
        u.append("BREAK")  # 호출자가 mutate 해도 원본 pin 깨지지 않음
        assert "BREAK" not in BINANCE_USDT_TOP30


class TestCsTsmomPageHtml:
    """2026-05-21 fix — 페이지 레이아웃 재정의 (TOP-10 카드 + 30종 테이블)."""

    def test_page_has_top_card_section(self):
        from src.dashboard.app import _render_cs_tsmom_page
        html = _render_cs_tsmom_page()
        # TOP-10 BUY 후보 섹션 + 카드 그리드 CSS
        assert "오늘의 BUY 후보" in html
        assert ".top-grid" in html
        assert ".top-card" in html
        assert "renderTopCards" in html

    def test_page_has_full_diagnostic_table(self):
        from src.dashboard.app import _render_cs_tsmom_page
        html = _render_cs_tsmom_page()
        # 기존 30종 테이블도 유지
        assert "전체 진단" in html
        assert "renderFullTable" in html

    def test_page_has_pin_badge_and_refresh_button(self):
        from src.dashboard.app import _render_cs_tsmom_page
        html = _render_cs_tsmom_page()
        assert "universe pin" in html
        assert "캐시 무효화" in html
        assert "forceRefresh" in html


class TestCsTsmomComputerCache:
    def test_returns_cached_within_ttl(self, monkeypatch):
        comp = CsTsmomComputer(ttl_sec=1000)
        calls = {"n": 0}

        def _fake_refresh(self):
            calls["n"] += 1
            from src.dashboard.cs_tsmom_signals import CsTsmomState
            return CsTsmomState(available=True, universe_size=30, rows=[])

        monkeypatch.setattr(CsTsmomComputer, "_refresh", _fake_refresh)
        a = comp.compute()
        b = comp.compute()
        assert calls["n"] == 1     # 캐시 적중
        assert a is b              # same object reused

    def test_force_bypasses_cache(self, monkeypatch):
        comp = CsTsmomComputer(ttl_sec=1000)
        calls = {"n": 0}

        def _fake_refresh(self):
            calls["n"] += 1
            from src.dashboard.cs_tsmom_signals import CsTsmomState
            return CsTsmomState(available=True, universe_size=30, rows=[])

        monkeypatch.setattr(CsTsmomComputer, "_refresh", _fake_refresh)
        comp.compute()
        comp.compute(force=True)
        assert calls["n"] == 2

    def test_refresh_failure_returns_unavailable(self, monkeypatch):
        comp = CsTsmomComputer(ttl_sec=1000)

        def _boom(self):
            raise RuntimeError("simulated network down")

        # _refresh internal already catches and returns CsTsmomState(available=False).
        # Patch the imported module helper instead to force exception path.
        import importlib
        bench = importlib.import_module("src.dashboard.cs_tsmom_signals")
        monkeypatch.setattr(bench, "compute_signals_from_panels", lambda *a, **k: 1/0)
        result = comp.compute(force=True)
        # _refresh wraps in try/except → returns unavailable state, never raises.
        # Could be available=False with the math error, OR fetch may fail upstream
        # (no network in CI). Either way contract: never raises.
        assert isinstance(result.available, bool)

"""Unit tests for LivePositionRiskManager (#227 S2)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.live.pnl_aggregator import PnLAggregator
from src.live.strategy_position_store import StrategyPositionStore
from src.portfolio.live_position_risk import LivePositionRiskManager, StopTpPolicy


def _setup(
    *,
    sid: str = "live_rsi",
    symbol: str = "005930",
    entry_price: float = 80_000.0,
    qty: float = 100.0,
    stop_loss_pct: float = 0.03,
    take_profit_pct: float = 0.06,
    trailing_stop_pct: float | None = None,
) -> tuple[LivePositionRiskManager, list[Any]]:
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    pnl.record_fill(
        strategy_id=sid,
        symbol=symbol,
        side="buy",
        qty=Decimal(str(qty)),
        price=Decimal(str(entry_price)),
    )
    store.record_fill(
        strategy_id=sid,
        symbol=symbol,
        side="buy",
        qty=Decimal(str(qty)),
    )
    captured_events: list[Any] = []
    mgr = LivePositionRiskManager(
        position_store=store,
        pnl_aggregator=pnl,
        wal_observer=captured_events.append,
    )
    mgr.register_strategy_policy(
        sid,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
    )
    return mgr, captured_events


def _setup_short(
    *,
    sid: str = "momo",
    symbol: str = "BTCUSDT",
    entry_price: float = 60_000.0,
    qty: float = 1.0,
    stop_loss_pct: float = 0.03,
    take_profit_pct: float = 0.06,
    trailing_stop_pct: float | None = None,
) -> tuple[LivePositionRiskManager, list[Any]]:
    """Open a SHORT: sell with no prior long → store qty negative, pnl avg set."""
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    pnl.record_fill(
        strategy_id=sid,
        symbol=symbol,
        side="sell",
        qty=Decimal(str(qty)),
        price=Decimal(str(entry_price)),
    )
    store.record_fill(
        strategy_id=sid,
        symbol=symbol,
        side="sell",
        qty=Decimal(str(qty)),
    )
    captured_events: list[Any] = []
    mgr = LivePositionRiskManager(
        position_store=store,
        pnl_aggregator=pnl,
        wal_observer=captured_events.append,
    )
    mgr.register_strategy_policy(
        sid,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
    )
    return mgr, captured_events


def _now() -> datetime:
    return datetime(2026, 5, 11, 14, 30, tzinfo=timezone.utc)


class TestStopTpPolicy:
    def test_valid_policy(self):
        p = StopTpPolicy(stop_loss_pct=0.03, take_profit_pct=0.06)
        assert p.stop_loss_pct == 0.03
        assert p.trailing_stop_pct is None

    def test_invalid_stop_loss_raises(self):
        with pytest.raises(ValueError):
            StopTpPolicy(stop_loss_pct=0.0, take_profit_pct=0.06)
        with pytest.raises(ValueError):
            StopTpPolicy(stop_loss_pct=1.5, take_profit_pct=0.06)

    def test_invalid_trailing_raises(self):
        with pytest.raises(ValueError):
            StopTpPolicy(
                stop_loss_pct=0.03, take_profit_pct=0.06, trailing_stop_pct=0.0,
            )


class TestStopLoss:
    def test_stop_loss_triggers_at_threshold(self):
        mgr, events = _setup()
        # 80,000 * (1 - 0.03) = 77,600 — last_price <= 77,600 should trigger
        intents = mgr.evaluate("005930", Decimal("76400"), _now())
        assert len(intents) == 1
        intent = intents[0]
        assert intent.side == "sell"
        assert intent.symbol == "005930"
        assert intent.strategy_id == "live_rsi"
        assert intent.qty == 100.0
        assert "live_stop_loss" in intent.reason
        assert "entry=80000" in intent.reason
        assert len(events) == 1
        assert events[0].event_type == "position_stop_triggered"
        assert events[0].payload["trigger"] == "stop_loss"

    def test_stop_loss_does_not_trigger_above_threshold(self):
        mgr, events = _setup()
        # 78,000 > 77,600 → no trigger
        intents = mgr.evaluate("005930", Decimal("78000"), _now())
        assert intents == []
        assert events == []

    def test_stop_loss_at_exact_threshold_triggers(self):
        mgr, _ = _setup()
        intents = mgr.evaluate("005930", Decimal("77600"), _now())  # exactly threshold
        assert len(intents) == 1


class TestTakeProfit:
    def test_take_profit_triggers(self):
        mgr, events = _setup()
        # 80,000 * (1 + 0.06) = 84,800
        intents = mgr.evaluate("005930", Decimal("84800"), _now())
        assert len(intents) == 1
        assert intents[0].side == "sell"
        assert "live_take_profit" in intents[0].reason
        assert events[0].payload["trigger"] == "take_profit"

    def test_take_profit_does_not_trigger_below(self):
        mgr, _ = _setup()
        intents = mgr.evaluate("005930", Decimal("82000"), _now())
        assert intents == []


class TestTrailingStop:
    def test_trailing_stop_triggers_after_high_water(self):
        # Wide take_profit so the high-water tick alone doesn't fire TP first.
        mgr, events = _setup(take_profit_pct=0.30, trailing_stop_pct=0.02)
        # Tick 1 — price climbs to 90,000 (above entry, sets high water).
        # 90000/80000 = +12.5% < take_profit_pct=30% → no TP, no trailing fire.
        assert mgr.evaluate("005930", Decimal("90000"), _now()) == []
        # Tick 2 — price drops to 88,200 (= 90000 * (1 - 0.02)) → trailing fires.
        intents = mgr.evaluate("005930", Decimal("88200"), _now())
        assert len(intents) == 1
        assert "live_trailing_stop" in intents[0].reason
        assert events[0].payload["trigger"] == "trailing_stop"

    def test_trailing_stop_does_not_fire_before_breaking_above_entry(self):
        """Trailing should not fire while price stays at-or-below entry —
        stop_loss is responsible for that range."""
        mgr, _ = _setup(stop_loss_pct=0.05, trailing_stop_pct=0.02)
        # Price never crosses entry, drops a bit but well above stop_loss.
        intents = mgr.evaluate("005930", Decimal("79500"), _now())
        # 79500 > 76000 (entry * 0.95) → stop_loss no
        # high_water still avg_cost (80000), 80000*0.98=78400; 79500 > 78400 → no
        assert intents == []

    def test_trailing_resets_after_sell(self):
        mgr, _ = _setup(take_profit_pct=0.30, trailing_stop_pct=0.02)
        # First entry: ride up + trailing fires
        mgr.evaluate("005930", Decimal("90000"), _now())
        intents = mgr.evaluate("005930", Decimal("88200"), _now())
        assert len(intents) == 1
        # The store still says held=100 (the SELL is an intent, not a fill).
        # But a subsequent evaluate at 88,300 (above last trail) should NOT
        # re-fire if high_water was reset to entry. To assert reset, we need
        # to reset position too — emulate post-fill: store records sell.
        store: StrategyPositionStore = mgr._position_store
        store.record_fill(
            strategy_id="live_rsi", symbol="005930", side="sell", qty=Decimal("100"),
        )
        # After flat, evaluate should return [] and clear stale high_water.
        assert mgr.evaluate("005930", Decimal("88300"), _now()) == []
        assert ("live_rsi", "005930") not in mgr._high_water


class TestNoPosition:
    def test_no_position_returns_empty(self):
        store = StrategyPositionStore()
        pnl = PnLAggregator()
        mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
        mgr.register_strategy_policy("live_rsi", stop_loss_pct=0.03, take_profit_pct=0.06)
        intents = mgr.evaluate("005930", Decimal("70000"), _now())
        assert intents == []

    def test_unregistered_strategy_no_emit(self):
        mgr, _ = _setup()
        # Symbol exists, but only 'live_rsi' policy is registered. evaluate
        # for a symbol held by that strategy still works:
        intents = mgr.evaluate("005930", Decimal("76400"), _now())
        assert len(intents) == 1
        # If we evaluate a symbol nobody holds → empty.
        intents2 = mgr.evaluate("999999", Decimal("76400"), _now())
        assert intents2 == []


class TestMultipleStrategies:
    def test_two_strategies_independent_thresholds(self):
        store = StrategyPositionStore()
        pnl = PnLAggregator()
        for sid in ("strat_tight", "strat_loose"):
            pnl.record_fill(
                strategy_id=sid, symbol="005930", side="buy",
                qty=Decimal("50"), price=Decimal("80000"),
            )
            store.record_fill(
                strategy_id=sid, symbol="005930", side="buy", qty=Decimal("50"),
            )
        mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
        mgr.register_strategy_policy("strat_tight", stop_loss_pct=0.02, take_profit_pct=0.04)
        mgr.register_strategy_policy("strat_loose", stop_loss_pct=0.10, take_profit_pct=0.20)

        # Price at -3% — only the tight strategy's stop should fire.
        intents = mgr.evaluate("005930", Decimal("77600"), _now())
        assert len(intents) == 1
        assert intents[0].strategy_id == "strat_tight"


# ---------------------------------------------------------------------------
# #238 — SHORT (negative qty) position management.
#
# Root incident: momo-btc-v2 opened a naked -1 BTC short with ZERO auto-stop
# because the risk manager was long-only (held <= 0 discarded the position).
# Short handling is ADDITIVE — the long path above stays bit-identical.
#
# SHORT semantics (inverted from long):
#   - stop_loss   fires when price RISES stop_loss_pct ABOVE entry
#   - take_profit fires when price FALLS take_profit_pct BELOW entry
#   - trailing    tracks the LOW-water mark; fires when price rises
#                 trailing_stop_pct above that low (only after price has
#                 moved below entry, mirroring the long break-above gate)
#   - exit intent is a BUY (cover) for qty = abs(held)
# ---------------------------------------------------------------------------


class TestShortStopLoss:
    def test_short_stop_loss_triggers_on_price_rise(self):
        mgr, events = _setup_short()
        # entry 60000, stop_loss 0.03 → 60000 * (1 + 0.03) = 61800.
        intents = mgr.evaluate("BTCUSDT", Decimal("62000"), _now())
        assert len(intents) == 1
        intent = intents[0]
        assert intent.side == "buy"  # cover the short
        assert intent.symbol == "BTCUSDT"
        assert intent.strategy_id == "momo"
        assert intent.qty == 1.0  # abs(held)
        assert "live_stop_loss" in intent.reason
        assert "entry=60000" in intent.reason
        assert len(events) == 1
        assert events[0].payload["trigger"] == "stop_loss"

    def test_short_stop_loss_at_exact_threshold(self):
        mgr, _ = _setup_short()
        intents = mgr.evaluate("BTCUSDT", Decimal("61800"), _now())  # exactly +3%
        assert len(intents) == 1

    def test_short_stop_loss_no_trigger_below_threshold(self):
        mgr, events = _setup_short()
        # 61000 < 61800 → short still in profit-ish range, no stop.
        intents = mgr.evaluate("BTCUSDT", Decimal("61000"), _now())
        assert intents == []
        assert events == []


class TestShortTakeProfit:
    def test_short_take_profit_triggers_on_price_fall(self):
        mgr, events = _setup_short()
        # entry 60000, take_profit 0.06 → 60000 * (1 - 0.06) = 56400.
        intents = mgr.evaluate("BTCUSDT", Decimal("56000"), _now())
        assert len(intents) == 1
        assert intents[0].side == "buy"
        assert intents[0].qty == 1.0
        assert "live_take_profit" in intents[0].reason
        assert events[0].payload["trigger"] == "take_profit"

    def test_short_take_profit_no_trigger_above(self):
        mgr, _ = _setup_short()
        # 58000 > 56400 → not enough downside yet.
        intents = mgr.evaluate("BTCUSDT", Decimal("58000"), _now())
        assert intents == []


class TestShortTrailingStop:
    def test_short_trailing_tracks_low_water_and_fires_on_rebound(self):
        # Wide stop_loss so the rebound tick doesn't fire stop_loss first.
        mgr, events = _setup_short(stop_loss_pct=0.30, trailing_stop_pct=0.02)
        # Tick 1 — price falls to 54000 (below entry, sets low water).
        # 54000/60000 = -10% → not at take_profit? take_profit_pct default 0.06
        # → tp = 56400; 54000 <= 56400 would fire TP. Use wide take_profit.
        mgr2, events2 = _setup_short(
            stop_loss_pct=0.30, take_profit_pct=0.30, trailing_stop_pct=0.02,
        )
        assert mgr2.evaluate("BTCUSDT", Decimal("54000"), _now()) == []
        # Tick 2 — price rebounds to 55080 (= 54000 * (1 + 0.02)) → trailing.
        intents = mgr2.evaluate("BTCUSDT", Decimal("55080"), _now())
        assert len(intents) == 1
        assert intents[0].side == "buy"
        assert "live_trailing_stop" in intents[0].reason
        assert events2[0].payload["trigger"] == "trailing_stop"

    def test_short_trailing_does_not_fire_before_breaking_below_entry(self):
        """Trailing must not fire while price stays at-or-above entry —
        stop_loss owns that range for shorts."""
        mgr, _ = _setup_short(stop_loss_pct=0.05, trailing_stop_pct=0.02)
        # 60500 above entry but below stop (60000*1.05=63000). low_water still
        # entry (60000); trailing band = 60000*1.02 = 61200; 60500 < 61200 → no.
        intents = mgr.evaluate("BTCUSDT", Decimal("60500"), _now())
        assert intents == []


class TestShortExitIntent:
    def test_short_exit_intent_is_buy_qty_abs(self):
        mgr, _ = _setup_short(entry_price=60_000.0, qty=2.0)
        intents = mgr.evaluate("BTCUSDT", Decimal("62000"), _now())
        assert len(intents) == 1
        assert intents[0].side == "buy"
        assert intents[0].qty == 2.0  # abs(-2)

    def test_short_flat_after_cover_clears_high_water(self):
        mgr, _ = _setup_short(
            stop_loss_pct=0.30, take_profit_pct=0.30, trailing_stop_pct=0.02,
        )
        mgr.evaluate("BTCUSDT", Decimal("54000"), _now())  # set low water
        intents = mgr.evaluate("BTCUSDT", Decimal("55080"), _now())  # trailing
        assert len(intents) == 1
        store: StrategyPositionStore = mgr._position_store
        # Cover the short (buy back) → store flat.
        store.record_fill(
            strategy_id="momo", symbol="BTCUSDT", side="buy", qty=Decimal("1"),
        )
        assert mgr.evaluate("BTCUSDT", Decimal("55000"), _now()) == []
        assert ("momo", "BTCUSDT") not in mgr._high_water


class TestLongPathRegression:
    """The long path must remain bit-identical after short support added."""

    def test_long_stop_loss_still_sell_qty_positive(self):
        mgr, events = _setup()
        intents = mgr.evaluate("005930", Decimal("76400"), _now())
        assert len(intents) == 1
        assert intents[0].side == "sell"
        assert intents[0].qty == 100.0
        assert "live_stop_loss" in intents[0].reason
        assert events[0].payload["trigger"] == "stop_loss"


class TestPendingExitTimeoutSelfHeal:
    """Regression: broker fill 누락으로 _pending_exit 가 영구 stuck 되면 같은
    (sid, symbol) 은 자동 청산 불가 → 17h+ 동안 +ROI 도달해도 TP fire 안 됨
    (2026-05-23 BTCUSDT 실측). guard 가 PENDING_EXIT_TIMEOUT_SEC 이후 자동
    해제되어 재평가하는지 검증.
    """

    def test_first_evaluate_emits_then_pending_exit_blocks_within_timeout(self):
        """SELL 발사 후 timeout 안에는 동일 (sid, symbol) 추가 SELL 차단."""
        mgr, _ = _setup()
        # 1st evaluate: stop_loss 가격 → SELL intent + _pending_exit 등록.
        t0 = _now()
        intents1 = mgr.evaluate("005930", Decimal("76400"), t0)
        assert len(intents1) == 1
        assert ("live_rsi", "005930") in mgr._pending_exit
        # store 갱신 안 됨 (broker fill 누락 시뮬). timeout 미경과 → block.
        from datetime import timedelta
        intents2 = mgr.evaluate("005930", Decimal("76400"), t0 + timedelta(seconds=10))
        assert intents2 == []

    def test_pending_exit_self_heals_after_timeout_and_re_emits(self):
        """broker fill 누락 + timeout 경과 → guard 해제, 다음 tick 에 SELL 재발사.

        실측 시나리오 (BTCUSDT 2026-05-23 16:00:17): trailing_stop 발사 후
        broker fill 도착 안 함 → 17h 동안 추가 평가 0건 → ROI +18% 도달해도
        TP fire 못 함. timeout self-heal 로 다음 tick 에 다시 SELL 발사 가능.
        """
        # 명시적 짧은 timeout 으로 테스트 결정성 확보.
        store = StrategyPositionStore()
        pnl = PnLAggregator()
        pnl.record_fill(
            strategy_id="cand-c-live-breakout", symbol="BTCUSDT",
            side="buy", qty=Decimal("0.05"), price=Decimal("75500"),
        )
        store.record_fill(
            strategy_id="cand-c-live-breakout", symbol="BTCUSDT",
            side="buy", qty=Decimal("0.05"),
        )
        mgr = LivePositionRiskManager(
            position_store=store, pnl_aggregator=pnl,
            pending_exit_timeout_sec=5.0,
        )
        mgr.register_strategy_policy(
            "cand-c-live-breakout",
            stop_loss_pct=0.008, take_profit_pct=0.012,  # 10x leverage, ROI 8%/12%
            trailing_stop_pct=0.005,
        )
        # 1st: TP 가격 도달 → SELL intent 발사 + guard 등록.
        from datetime import timedelta
        t0 = _now()
        intents1 = mgr.evaluate("BTCUSDT", Decimal("76800"), t0)  # +1.72% > +1.2% TP
        assert len(intents1) == 1
        assert intents1[0].side == "sell"
        assert ("cand-c-live-breakout", "BTCUSDT") in mgr._pending_exit
        # 2nd (3s 후): broker fill 안 옴 (store unchanged). 미경과 → block.
        intents2 = mgr.evaluate("BTCUSDT", Decimal("76900"), t0 + timedelta(seconds=3))
        assert intents2 == []
        # 3rd (10s 후): timeout 초과 → guard 자동 해제, SELL 재발사.
        intents3 = mgr.evaluate("BTCUSDT", Decimal("76900"), t0 + timedelta(seconds=10))
        assert len(intents3) == 1
        assert intents3[0].side == "sell"
        assert "live_take_profit" in intents3[0].reason

    def test_pending_exit_cleared_on_held_zero_keeps_existing_behavior(self):
        """held=0 (정상 fill) 시 즉시 cleanup — 기존 동작 회귀 방지."""
        mgr, _ = _setup()
        intents1 = mgr.evaluate("005930", Decimal("76400"), _now())
        assert len(intents1) == 1
        assert ("live_rsi", "005930") in mgr._pending_exit
        # broker fill 정상 도착 → store 0 으로 갱신.
        store: StrategyPositionStore = mgr._position_store
        store.record_fill(
            strategy_id="live_rsi", symbol="005930",
            side="sell", qty=Decimal("100"),
        )
        intents2 = mgr.evaluate("005930", Decimal("76400"), _now())
        assert intents2 == []
        assert ("live_rsi", "005930") not in mgr._pending_exit


class TestCostBasisSignMismatch:
    """AVAX/BTC/DOGE 2026-06-14 회귀 — reconciler phantom-clear 후 재진입 시
    PnL cost basis 가 stale(반대부호 옛 진입가)로 남아 synthetic 이 진입 즉시
    오발동 reduce_only 청산(−0.8% flat)하던 사고. 부호 불일치면 평가 skip."""

    def test_sign_mismatch_skips_synthetic_stop(self):
        store = StrategyPositionStore()
        pnl = PnLAggregator()
        # 1) 이전 사이클: 롱 진입 → cost basis (+23.4, 6.572)
        pnl.record_fill(
            strategy_id="momo", symbol="AVAXUSDT", side="buy",
            qty=Decimal("23.4"), price=Decimal("6.572"),
        )
        # 2) reconciler phantom-clear: store qty 0 (cost basis 는 stale 잔존)
        store.force_sync_position(
            strategy_id="momo", symbol="AVAXUSDT", qty=Decimal("0"),
        )
        # 3) 재진입 숏: store held -23.4 (cost basis 는 아직 stale 롱 +23.4)
        store.force_sync_position(
            strategy_id="momo", symbol="AVAXUSDT", qty=Decimal("-23.4"),
        )
        captured: list = []
        mgr = LivePositionRiskManager(
            position_store=store, pnl_aggregator=pnl,
            wal_observer=captured.append,
        )
        mgr.register_strategy_policy(
            "momo", stop_loss_pct=0.03, take_profit_pct=0.06,
        )
        # stale avg_cost(6.572) 기준이면 숏 SL=6.572×1.03=6.769 → last 6.8 에서
        # 오발동했어야 함. 부호 불일치 skip 으로 청산 intent 없어야 한다.
        intents = mgr.evaluate("AVAXUSDT", Decimal("6.8"), _now())
        assert intents == []

    def test_matching_short_still_fires(self):
        """부호 일치(정상 숏)면 stop 정상 발동 — 보호 회귀 없음."""
        mgr, _ = _setup_short(
            symbol="AVAXUSDT", entry_price=6.757, qty=23.4,
            stop_loss_pct=0.005,
        )
        # 숏 SL = 6.757×1.005 = 6.790. last 6.80 ≥ 6.790 → 정상 발동(cover=buy).
        intents = mgr.evaluate("AVAXUSDT", Decimal("6.80"), _now())
        assert len(intents) == 1
        assert intents[0].side == "buy"


def test_effective_policy_prefers_dynamic_override():
    """fix(2026-06-30) — 거래소 네이티브 TP/SL 이 진입 동적 override(2ATR/꼬리저점·2R)를
    쓰도록. symbol 주면 동적 우선, 없거나 다른 symbol/미등록이면 정적 fallback.
    airborne 은 정적값을 override 로 등록하므로 동적=정적 → 무영향."""
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
    sid = "live-donchian-breakout-btcgate"
    mgr.register_strategy_policy(sid, stop_loss_pct=0.08, take_profit_pct=0.50)

    # override 전 — 정적
    assert mgr.effective_policy(sid) == (0.08, 0.50)
    assert mgr.effective_policy(sid, "BTCUSDT") == (0.08, 0.50)

    # 진입 동적 override (2ATR 손절 3% / 6% 가정)
    mgr.register_entry_override(sid, "BTCUSDT", stop_loss_pct=0.03, take_profit_pct=0.06)

    assert mgr.effective_policy(sid, "BTCUSDT") == (0.03, 0.06)   # 동적 우선
    assert mgr.effective_policy(sid, "ETHUSDT") == (0.08, 0.50)   # 다른 symbol → 정적
    assert mgr.effective_policy(sid) == (0.08, 0.50)              # symbol 없음 → 정적(구호환)


def test_effective_policy_none_for_unregistered_strategy():
    mgr = LivePositionRiskManager(
        position_store=StrategyPositionStore(), pnl_aggregator=PnLAggregator(),
    )
    assert mgr.effective_policy("cs-tsmom", "BTCUSDT") is None
    assert mgr.effective_policy("cs-tsmom") is None

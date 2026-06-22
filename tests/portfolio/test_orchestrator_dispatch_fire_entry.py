"""AsyncStrategyOrchestrator.dispatch_fire_entry — 발화 직접진입 (2026-06-11).

봉루프 decouple 한 airborne consume 의 단일 발화 진입. run_bar 의 진입 로직
(_live_entered dedup / stop cooldown / max_concurrent cap / sizing / policy /
preset meta) 을 재사용하되 Signal 자체평가 대신 발화 side/price 사용.

상세: docs/specs/airborne-fire-driven-consume.md.
"""
from __future__ import annotations

from typing import ClassVar

import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from portfolio import AsyncStrategyOrchestrator
import portfolio._async_orchestrator as orch_mod
from risk.dsl import Policy


def _orch(**kw) -> AsyncStrategyOrchestrator:
    return AsyncStrategyOrchestrator(Policy(policy_version=1, name="test"), **kw)


class _BidirScanner(LiveScannerMixin):
    """bidir live-scanner — long/short 둘 다. stop/TP/default_size 명시."""

    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06
    shorts_allowed: ClassVar[bool] = True
    default_size: float = 0.05

    async def on_bar(self, ctx) -> Signal:  # pragma: no cover — fire 경로 미사용
        return Signal(action="hold", size=0.0, reason="unused")


_TS = "2026-06-11T22:00:00+00:00"


def test_long_fire_returns_buy_intent_with_qty_and_preset_meta():
    orch = _orch()
    orch.register_strategy("live-airborne-x", _BidirScanner())

    intent = orch.dispatch_fire_entry(
        "live-airborne-x", "SOLUSDT", "long",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert intent is not None, "long 발화 → OrderIntent"
    assert intent.side == "buy"
    assert intent.strategy_id == "live-airborne-x"
    assert intent.symbol == "SOLUSDT"
    # qty = (0.05 * 10000) / 100 = 5.0 (step round-down)
    assert intent.qty == pytest.approx(5.0)
    assert intent.reduce_only is False
    # preset meta — long: SL=price*(1-0.03), TP=price*(1+0.06)
    assert intent.meta is not None
    assert intent.meta["preset_sl_price"] == pytest.approx(97.0)
    assert intent.meta["preset_tp_price"] == pytest.approx(106.0)
    # (sid, symbol) live_entered 기록됨
    assert ("live-airborne-x", "SOLUSDT") in orch._live_entered


def test_short_fire_returns_sell_intent_short_preset():
    orch = _orch()
    orch.register_strategy("live-airborne-x", _BidirScanner())

    intent = orch.dispatch_fire_entry(
        "live-airborne-x", "DOGEUSDT", "short",
        price=200.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert intent is not None
    assert intent.side == "sell"
    # shorts_allowed=True → reduce_only False (숏 진입)
    assert intent.reduce_only is False
    # short preset: SL=price*(1+0.03)=206, TP=price*(1-0.06)=188
    assert intent.meta["preset_sl_price"] == pytest.approx(206.0)
    assert intent.meta["preset_tp_price"] == pytest.approx(188.0)


def test_second_call_same_sid_symbol_returns_none_live_entered():
    orch = _orch()
    orch.register_strategy("live-airborne-x", _BidirScanner())

    first = orch.dispatch_fire_entry(
        "live-airborne-x", "SOLUSDT", "long",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert first is not None
    second = orch.dispatch_fire_entry(
        "live-airborne-x", "SOLUSDT", "long",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert second is None, "이미 진입한 (sid,symbol) → None (1포지션)"


def test_cross_airborne_symbol_dedup_blocks_second_strategy():
    """다른 airborne 전략이 보유한 종목은 진입 차단 (2026-06-22, 네팅 desync 방지).

    kst-hours 가 SUI 숏 보유 중이면 short-whitelist 가 같은 SUI 진입 못 함 →
    거래소 네팅 holders=2 (store -2940 vs broker -880, 40804 폭주) 차단.
    """
    orch = _orch()
    orch.register_strategy("live-airborne-a", _BidirScanner())
    orch.register_strategy("live-airborne-b", _BidirScanner())
    first = orch.dispatch_fire_entry(
        "live-airborne-a", "SUIUSDT", "short",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert first is not None
    second = orch.dispatch_fire_entry(
        "live-airborne-b", "SUIUSDT", "short",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert second is None, "다른 airborne 보유 종목 → 차단"
    assert ("live-airborne-b", "SUIUSDT") not in orch._live_entered


def test_cross_airborne_dedup_scoped_to_airborne_only():
    """비-airborne(cs-tsmom 등) 보유 종목은 airborne 진입 안 막음 (크로스슬리브 영향 0)."""
    orch = _orch()
    orch.register_strategy("live-airborne-a", _BidirScanner())
    # 다른 슬리브가 SOL 보유 중 (다른 계좌/심볼체계 — 차단 대상 아님)
    orch._live_entered.add(("cs-tsmom-crypto-daily", "SOLUSDT"))
    intent = orch.dispatch_fire_entry(
        "live-airborne-a", "SOLUSDT", "short",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert intent is not None, "비-airborne 보유는 airborne 진입 안 막음"


def test_qty_none_path_returns_none_and_releases_live_entered():
    """사이징 drop(min-notional 미달) → None + live_entered 미잔존(재시도 허용)."""
    orch = _orch()
    orch.register_strategy("live-airborne-x", _BidirScanner())

    # equity 1.0 * 0.05 / 100 = 0.0005 coins → notional 0.05 USDT < min 5 → drop.
    intent = orch.dispatch_fire_entry(
        "live-airborne-x", "SOLUSDT", "long",
        price=100.0, ts=_TS, equity_usdt=1.0,
    )
    assert intent is None
    # 미발주이므로 live_entered 에서 해제돼 다음 발화 재시도 가능.
    assert ("live-airborne-x", "SOLUSDT") not in orch._live_entered


def test_unregistered_strategy_returns_none():
    orch = _orch()
    assert orch.dispatch_fire_entry(
        "ghost", "SOLUSDT", "long",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    ) is None


def test_disabled_strategy_returns_none():
    orch = _orch()
    orch.register_strategy("live-airborne-x", _BidirScanner())
    orch.disable_strategy("live-airborne-x")
    assert orch.dispatch_fire_entry(
        "live-airborne-x", "SOLUSDT", "long",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    ) is None


def test_stop_cooldown_blocks_fire_entry(monkeypatch):
    """release_live_position 으로 cooldown 기록 시 그 안의 발화 진입 차단."""
    fake = {"t": 1000.0}
    monkeypatch.setattr(orch_mod.time, "monotonic", lambda: fake["t"])

    class _CooldownScanner(_BidirScanner):
        cooldown_after_stop_sec: ClassVar[float] = 300.0

    orch = _orch()
    orch.register_strategy("live-airborne-x", _CooldownScanner())

    first = orch.dispatch_fire_entry(
        "live-airborne-x", "SOLUSDT", "long",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert first is not None
    orch.release_live_position("live-airborne-x", "SOLUSDT")

    fake["t"] += 60.0  # cooldown 안
    blocked = orch.dispatch_fire_entry(
        "live-airborne-x", "SOLUSDT", "long",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert blocked is None, "cooldown 안 발화 진입 차단"

    fake["t"] += 300.0  # 만료
    allowed = orch.dispatch_fire_entry(
        "live-airborne-x", "SOLUSDT", "long",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert allowed is not None, "cooldown 만료 후 재진입 허용"


def test_max_concurrent_cap_blocks_new_symbol():
    """max_concurrent_positions 캡 도달 시 신규 종목 발화 진입 차단."""
    class _CappedScanner(_BidirScanner):
        max_concurrent_positions: ClassVar[int] = 1

    orch = _orch()
    orch.register_strategy("live-airborne-x", _CappedScanner())

    a = orch.dispatch_fire_entry(
        "live-airborne-x", "SOLUSDT", "long",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert a is not None
    b = orch.dispatch_fire_entry(
        "live-airborne-x", "DOGEUSDT", "long",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert b is None, "캡 1 도달 → 신규 종목 차단"


def test_on_entry_callback_invoked_with_strategy_pcts():
    """fire 진입 시 _on_entry 콜백이 전략 stop/TP/trailing pct 로 호출."""
    calls: list = []

    orch = _orch()
    orch._on_entry = lambda sid, sym, **kw: calls.append((sid, sym, kw))
    orch.register_strategy("live-airborne-x", _BidirScanner())

    orch.dispatch_fire_entry(
        "live-airborne-x", "SOLUSDT", "long",
        price=100.0, ts=_TS, equity_usdt=10_000.0,
    )
    assert len(calls) == 1
    sid, sym, kw = calls[0]
    assert sid == "live-airborne-x" and sym == "SOLUSDT"
    assert kw["stop_loss_pct"] == pytest.approx(0.03)
    assert kw["take_profit_pct"] == pytest.approx(0.06)

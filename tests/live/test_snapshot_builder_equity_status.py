"""#238 follow-up — venue-inert visibility for SnapshotBuilder._inject_real_equity.

When a balance_provider is wired but a venue's real equity is unavailable
(provider error, ok:False, or cash/balance <= 0), the orchestrator's
fraction→qty conversion silently DROPS every order for that venue. The
operator then sees "0 trades" with no surfaced reason.

These tests pin the visibility contract (no math change):
  - `last_equity_status` is populated per venue with ok / reason / equity
  - a WARNING is emitted once per state-change, not per tick (throttle)
  - bit-identical no-op when balance_provider is None (the default)
"""
from __future__ import annotations

import logging
from decimal import Decimal

from src.live.snapshot_builder import SnapshotBuilder, SnapshotBuilderConfig
from src.live.types import Tick


def _tick(symbol: str = "BTCUSDT") -> Tick:
    return Tick(symbol=symbol, price=Decimal("80000"), qty=Decimal("1"),
                ts="2026-05-17T01:00:00+00:00")


class _FakeProvider:
    def __init__(self, payload, *, raises: bool = False):
        self._payload = payload
        self._raises = raises
        self.calls = 0

    def fetch(self):
        self.calls += 1
        if self._raises:
            raise RuntimeError("balance fetch boom")
        return self._payload


def test_no_provider_leaves_status_empty_and_bit_identical():
    """Default (no balance_provider) → last_equity_status empty, no warning."""
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None)
    snap = b.build_snapshot(_tick())
    assert snap.get("equity_usdt") in (None, 0.0)
    assert b.last_equity_status == {}


def test_ok_venue_records_ok_status():
    prov = _FakeProvider({
        "binance": {"ok": True, "available_usdt": 4610.9},
        "kis": {"ok": True, "cash_balance": 1_000_000},
    })
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov)
    b.build_snapshot(_tick())
    bn = b.last_equity_status["binance"]
    assert bn["ok"] is True
    assert bn["equity"] == 4610.9
    ki = b.last_equity_status["kis"]
    assert ki["ok"] is True
    assert ki["equity"] == 1_000_000.0


def test_ok_false_venue_records_reason_from_error():
    prov = _FakeProvider({
        "binance": {"ok": False, "error": "Binance 자격증명 누락"},
        "kis": {"ok": True, "cash_balance": 500_000},
    })
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov)
    b.build_snapshot(_tick())
    bn = b.last_equity_status["binance"]
    assert bn["ok"] is False
    assert "자격증명" in bn["reason"]
    assert b.last_equity_status["kis"]["ok"] is True


def test_ok_true_but_balance_non_positive_is_inert():
    prov = _FakeProvider({
        "binance": {"ok": True, "available_usdt": 0.0},
        "kis": {"ok": True, "cash_balance": 0},
    })
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov)
    b.build_snapshot(_tick())
    bn = b.last_equity_status["binance"]
    assert bn["ok"] is False
    assert "<=0" in bn["reason"] or "0" in bn["reason"]
    ki = b.last_equity_status["kis"]
    assert ki["ok"] is False


def test_provider_error_records_inert_for_all():
    prov = _FakeProvider(None, raises=True)
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov)
    b.build_snapshot(_tick())  # must not raise
    assert b.last_equity_status["binance"]["ok"] is False
    assert b.last_equity_status["kis"]["ok"] is False


def test_warning_emitted_once_per_state_change_not_per_tick(caplog):
    """INERT WARNING must throttle — log only when the venue state changes."""
    prov = _FakeProvider({
        "binance": {"ok": False, "error": "creds"},
        "kis": {"ok": True, "cash_balance": 100},
    })
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov)
    with caplog.at_level(logging.WARNING, logger="src.live.snapshot_builder"):
        for _ in range(5):
            b.build_snapshot(_tick())
    inert_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "INERT" in r.getMessage()
        and "binance" in r.getMessage()
    ]
    assert len(inert_warnings) == 1, (
        f"expected 1 throttled WARNING, got {len(inert_warnings)}"
    )


def test_real_kis_payload_shape_overlays_equity_krw():
    """Regression — the EXACT AccountInfoProvider.fetch()["kis"] shape from
    the user's live env must overlay snapshot["equity_krw"].

    Live evidence: standalone fetch returns this; the in-daemon pipeline
    failed to surface it. This pins the contract end-to-end.
    """
    prov = _FakeProvider({
        "kis": {
            "ok": True, "paper": True, "cano_masked": "5012****-01",
            "cash_balance": 9734720, "eval_amount": 10018720,
            "n_positions": 1, "rt_cd": "0",
        },
        "binance": {"ok": False, "error": "creds"},
    })
    b = SnapshotBuilder(["005930"], kis_client=None, balance_provider=prov)
    snap = b.build_snapshot(_tick("005930"))
    assert snap["equity_krw"] == 9734720.0
    assert b.last_equity_status["kis"]["ok"] is True


def test_last_known_good_equity_survives_transient_provider_failure():
    """Root-cause fix — once a venue's real equity is observed, a LATER
    transient ok:False (REST contention in the live daemon) must NOT
    regress the snapshot back to the tiny placeholder.

    This is the actual break: standalone (no load) the per-pipeline
    AccountInfoProvider succeeds; in-daemon under KIS-REST contention it
    transiently fails and the old code dropped back to the placeholder →
    KIS orders dropped → "0 trades".
    """
    payloads = [
        {"kis": {"ok": True, "cash_balance": 9734720}, "binance": {"ok": False}},
        # transient failure (EGW00201 / rate-limit under feed contention)
        {"kis": {"ok": False, "error": "EGW00201 rate-limit"}, "binance": {"ok": False}},
        {"kis": {"ok": False, "error": "EGW00201 rate-limit"}, "binance": {"ok": False}},
    ]

    class _Seq:
        def __init__(self):
            self.i = 0

        def fetch(self):
            p = payloads[min(self.i, len(payloads) - 1)]
            self.i += 1
            return p

    b = SnapshotBuilder(["005930"], kis_client=None, balance_provider=_Seq(),
                        config=SnapshotBuilderConfig(equity_krw=100000.0))
    s1 = b.build_snapshot(_tick("005930"))
    assert s1["equity_krw"] == 9734720.0
    # transient fail — must hold last-known-good, NOT regress to 100000.
    s2 = b.build_snapshot(_tick("005930"))
    assert s2["equity_krw"] == 9734720.0, "regressed to placeholder on transient fail"
    s3 = b.build_snapshot(_tick("005930"))
    assert s3["equity_krw"] == 9734720.0
    # status still reports the venue as degraded so the operator sees it,
    # but trading is NOT silently killed.
    assert b.last_equity_status["kis"]["ok"] is False


def test_no_known_good_then_failure_stays_placeholder_safe_drop():
    """If equity was NEVER observed, a failure must still leave the
    placeholder (no fabricated equity) so the conversion safely drops."""
    prov = _FakeProvider({"kis": {"ok": False, "error": "creds"},
                          "binance": {"ok": False, "error": "creds"}})
    b = SnapshotBuilder(["005930"], kis_client=None, balance_provider=prov,
                        config=SnapshotBuilderConfig(equity_krw=100000.0))
    snap = b.build_snapshot(_tick("005930"))
    assert snap["equity_krw"] == 100000.0  # untouched placeholder
    assert snap.get("equity_usdt") in (None, 0.0)


def test_warning_re_emits_when_state_transitions_back_to_ok(caplog):
    """ok:False → ok:True is a state change → a recovery line is allowed."""
    payloads = [
        {"binance": {"ok": False, "error": "creds"}, "kis": {"ok": True, "cash_balance": 1}},
        {"binance": {"ok": True, "available_usdt": 50.0}, "kis": {"ok": True, "cash_balance": 1}},
        {"binance": {"ok": False, "error": "creds"}, "kis": {"ok": True, "cash_balance": 1}},
    ]

    class _Seq:
        def __init__(self):
            self.i = 0

        def fetch(self):
            p = payloads[min(self.i, len(payloads) - 1)]
            self.i += 1
            return p

    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=_Seq())
    with caplog.at_level(logging.WARNING, logger="src.live.snapshot_builder"):
        b.build_snapshot(_tick())  # inert
        b.build_snapshot(_tick())  # recovered
        b.build_snapshot(_tick())  # inert again
    inert = [
        r for r in caplog.records
        if "INERT" in r.getMessage() and "binance" in r.getMessage()
    ]
    # Two distinct ok:False transitions → two INERT warnings (not 1, not 3).
    assert len(inert) == 2

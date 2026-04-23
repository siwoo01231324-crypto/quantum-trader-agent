from __future__ import annotations

import pytest

from src.execution.base import TimeInForce


def test_day_exists():
    assert TimeInForce.DAY == "DAY"


def test_ioc_exists():
    assert TimeInForce.IOC == "IOC"


def test_fok_exists():
    assert TimeInForce.FOK == "FOK"


def test_gtc_alias_equals_day():
    assert TimeInForce.GTC == TimeInForce.DAY
    assert TimeInForce.GTC.value == "DAY"


def test_gtx_post_only_exists():
    assert TimeInForce.GTX is not None
    assert TimeInForce.GTX.value == "GTX"


def test_gtd_exists():
    assert TimeInForce.GTD is not None
    assert TimeInForce.GTD.value == "GTD"


def test_existing_tests_not_broken():
    # Ensure DAY/IOC/FOK still work as before
    from src.execution.base import ChildOrder, Side
    from datetime import datetime
    order = ChildOrder(
        parent_id="p1",
        symbol="005930",
        side=Side.BUY,
        qty=10,
        price=None,
        tif=TimeInForce.DAY,
    )
    assert order.tif == TimeInForce.DAY
    assert order.tif == TimeInForce.GTC  # alias check

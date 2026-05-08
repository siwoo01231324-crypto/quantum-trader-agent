"""Tests for scripts/telegram_rebal.py format_digest (#218 Phase 4)."""
from __future__ import annotations

import sys
from pathlib import Path

# Add scripts/ to path for module import
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from telegram_rebal import format_digest  # noqa: E402


def test_basic_digest_format():
    msg = format_digest(
        "cs_tsmom_kr_daily",
        buys=["005930", "000660"],
        sells=["247540"],
        held=["035420", "035720"],
        n_submitted=3, n_rejected=0,
    )
    assert "cs_tsmom_kr_daily" in msg
    assert "매수 2종" in msg
    assert "매도 1종" in msg
    assert "유지 2종" in msg
    assert "005930" in msg
    assert "247540" in msg
    assert "3 제출 / 0 거부" in msg


def test_empty_lists_show_em_dash():
    msg = format_digest("cs_test", buys=[], sells=[], held=[])
    # 매수 0종 + 종목 부분이 "—" 이어야 함
    assert "매수 0종: —" in msg
    assert "매도 0종: —" in msg
    assert "유지 0종: —" in msg


def test_long_lists_truncated_with_count():
    """20 종목 → 5 만 표시 + (+15) 표기."""
    buys = [f"S{i:03d}" for i in range(20)]
    msg = format_digest("cs_test", buys=buys, sells=[], held=[])
    assert "S000" in msg
    assert "S004" in msg
    assert "+15" in msg  # 5개 표시 후 나머지 15개 카운트
    assert "S019" not in msg  # truncated


def test_pnl_signed_correctly():
    msg_pos = format_digest("s", buys=[], sells=[], held=[], portfolio_pnl_pct=0.025)
    assert "+2.50%" in msg_pos
    msg_neg = format_digest("s", buys=[], sells=[], held=[], portfolio_pnl_pct=-0.013)
    assert "-1.30%" in msg_neg


def test_no_orders_section_when_zero():
    """submitted=0 + rejected=0 → '주문' 라인 미출력."""
    msg = format_digest("s", buys=["A"], sells=[], held=[],
                        n_submitted=0, n_rejected=0)
    assert "제출" not in msg


def test_message_under_4096_chars_with_long_universe():
    """매우 긴 universe (350 종목) 도 메시지 4096 자 미만 보장."""
    syms = [f"T{i:03d}" for i in range(350)]
    msg = format_digest("cs_huge", buys=syms[:100], sells=syms[100:200],
                        held=syms[200:350])
    # max_symbol_preview default 5 → 각 줄 매우 짧음
    assert len(msg) < 1000

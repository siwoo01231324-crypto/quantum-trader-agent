from __future__ import annotations

import hashlib
import re

BINANCE_CLIENT_ID_PATTERN = r"^[\.A-Z\:/a-z0-9_-]{1,36}$"
_COMPILED = re.compile(BINANCE_CLIENT_ID_PATTERN)

_MAX_LEN = 36


def generate(strategy: str, symbol: str, side: str, ts_ms: int) -> str:
    """Generate a deterministic Binance-compatible client_order_id.

    Uses SHA-256 of (strategy, symbol, side, ts_ms) truncated to 36 chars.
    Output chars are restricted to hex digits (0-9, a-f) which satisfy the regex.
    """
    raw = f"{strategy}:{symbol}:{side}:{ts_ms}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    cid = digest[:_MAX_LEN]
    assert _COMPILED.match(cid), f"Generated ID '{cid}' does not match Binance pattern"
    return cid

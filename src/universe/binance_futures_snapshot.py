"""Binance USDM Futures 24h ticker snapshot fetcher.

Maps the public ``GET /fapi/v1/ticker/24hr`` response to the DataFrame schema
consumed by :func:`src.universe.binance_top.top_n_by_volume`. Pure mapping +
thin async HTTP fetcher — no auth required.

Used by ``scripts/airborne_alert_daemon.py`` to refresh the USDT-perp top-N
universe periodically (see ``docs/specs/live-airborne-alert-daemon.md``).
"""
from __future__ import annotations

import httpx
import pandas as pd

REST_BASE_LIVE = "https://fapi.binance.com"
REST_BASE_TESTNET = "https://testnet.binancefuture.com"

_SNAPSHOT_COLUMNS = ("symbol", "last_price", "change_24h_pct", "quote_volume_24h")


def map_ticker24h_to_snapshot(raw: list[dict]) -> pd.DataFrame:
    """Pure mapping: Binance fapi /ticker/24hr payload → binance_top snapshot schema.

    Skips malformed entries (missing/non-numeric fields) defensively — the WS
    daemon should not crash on a single bad row from the exchange.
    """
    rows: list[dict] = []
    for r in raw:
        try:
            rows.append({
                "symbol": str(r["symbol"]),
                "last_price": float(r["lastPrice"]),
                "change_24h_pct": float(r["priceChangePercent"]),
                "quote_volume_24h": float(r["quoteVolume"]),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return pd.DataFrame(rows, columns=list(_SNAPSHOT_COLUMNS))


async def fetch_futures_24h_snapshot(
    *,
    base_url: str = REST_BASE_LIVE,
    timeout: float = 10.0,
    client: httpx.AsyncClient | None = None,
) -> pd.DataFrame:
    """Fetch the live 24h ticker snapshot from Binance USDM Futures REST.

    Args:
        base_url: fapi base. Default = live (``REST_BASE_LIVE``). Use
            ``REST_BASE_TESTNET`` for testnet.
        timeout: HTTP request timeout (seconds).
        client: optional injected ``httpx.AsyncClient`` — if ``None`` a transient
            client is created. Inject for connection pooling across calls.

    Returns:
        DataFrame with columns ``symbol, last_price, change_24h_pct, quote_volume_24h``.
    """
    url = f"{base_url.rstrip('/')}/fapi/v1/ticker/24hr"
    if client is None:
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.get(url)
    else:
        resp = await client.get(url, timeout=timeout)
    resp.raise_for_status()
    return map_ticker24h_to_snapshot(resp.json())


__all__ = [
    "REST_BASE_LIVE",
    "REST_BASE_TESTNET",
    "map_ticker24h_to_snapshot",
    "fetch_futures_24h_snapshot",
]

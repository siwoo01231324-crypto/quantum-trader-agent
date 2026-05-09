"""Binance USDT spot top-N 거래량 universe builder.

Pure function: 24h 거래량 스냅샷 DataFrame 을 받아서 거래량 상위 N 심볼 리스트
반환. 외부 API 호출 없음 — 호출자가 어댑터 통해 스냅샷 공급.

Snapshot 어댑터는 `scripts/fetch_binance_volume_snapshot.py` 또는 `src/data_lake/`
산하의 fetcher 가 책임진다.

snapshot_df 컬럼 규약:
- `symbol` (str, e.g., "BTCUSDT")
- `last_price` (float)
- `change_24h_pct` (float, 24h 가격 변동 %)
- `quote_volume_24h` (float, 24h quote (USDT) 거래대금)

기본 제외 규칙:
- 스테이블코인 base: USDC/USD1/FDUSD/BUSD/TUSD/DAI/USDP/PYUSD/USDD
- 합성·페그: PAXG (gold) / XAUT
- 정지·소멸: LUNC, USTC, FTT
- 레버리지 토큰 suffix: UPUSDT/DOWNUSDT/BULLUSDT/BEARUSDT
- 가격 ≈ $1 ± 1% AND 24h 변동 < 0.5% → stablecoin-like 자동 감지

이 모든 규칙은 인자로 override 가능하며 default 는 모듈 상수.
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd

REQUIRED_COLUMNS = ("symbol", "last_price", "change_24h_pct", "quote_volume_24h")

DEFAULT_EXCLUDED_BASES: frozenset[str] = frozenset({
    # Stablecoins / fiat-pegged
    "USDC", "USD1", "FDUSD", "BUSD", "TUSD", "DAI", "USDP", "PYUSD", "USDD",
    # Commodity-pegged
    "PAXG", "XAUT",
    # Defunct / collapsed
    "LUNC", "USTC", "FTT",
})

DEFAULT_EXCLUDED_SUFFIXES: tuple[str, ...] = (
    "UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT",
)

_QUOTE = "USDT"


def _validate(snapshot: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in snapshot.columns]
    if missing:
        raise ValueError(
            f"snapshot DataFrame missing required columns: {missing}. "
            f"Required: {REQUIRED_COLUMNS}"
        )


def _is_stable_like(price: float, change_pct: float) -> bool:
    """가격이 $1 근방이고 24h 변동이 거의 없으면 stable 추정."""
    return 0.98 <= price <= 1.02 and abs(change_pct) < 0.5


def _base_of(symbol: str) -> str:
    if symbol.endswith(_QUOTE):
        return symbol[: -len(_QUOTE)]
    return symbol


def top_n_by_volume(
    snapshot: pd.DataFrame,
    n: int,
    *,
    excluded_bases: Iterable[str] | None = None,
    excluded_suffixes: Iterable[str] | None = None,
) -> list[str]:
    """USDT 페어 24h 거래량 상위 N 심볼.

    Args:
        snapshot: 24h ticker 스냅샷 DataFrame. REQUIRED_COLUMNS 필요.
        n: 상위 몇 심볼.
        excluded_bases: 제외할 base asset 집합 (기본 = DEFAULT_EXCLUDED_BASES)
        excluded_suffixes: 제외할 심볼 suffix 들 (레버리지 토큰)
                          (기본 = DEFAULT_EXCLUDED_SUFFIXES)

    Returns:
        list[str] — 거래량 내림차순 상위 N 심볼 (예: ["BTCUSDT", "ETHUSDT", ...]).
    """
    _validate(snapshot)
    if n <= 0:
        return []

    bases = frozenset(excluded_bases) if excluded_bases is not None else DEFAULT_EXCLUDED_BASES
    suffixes = tuple(excluded_suffixes) if excluded_suffixes is not None else DEFAULT_EXCLUDED_SUFFIXES

    df = snapshot.copy()
    df = df[df["symbol"].astype(str).str.endswith(_QUOTE)]

    # Suffix 제외 (레버리지)
    df = df[~df["symbol"].apply(lambda s: any(s.endswith(suf) for suf in suffixes))]

    # Base 제외 (스테이블/페그/소멸)
    df = df[~df["symbol"].apply(lambda s: _base_of(s) in bases)]

    # 가격·변동률 기반 stable-like 자동 감지
    df = df[~df.apply(
        lambda r: _is_stable_like(float(r["last_price"]), float(r["change_24h_pct"])),
        axis=1,
    )]

    df = df.sort_values("quote_volume_24h", ascending=False).head(n)
    return df["symbol"].astype(str).tolist()

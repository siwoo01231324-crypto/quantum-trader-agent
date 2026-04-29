"""KRX pool sampler — round-robin sector-balanced selection from KOSPI200."""
from __future__ import annotations

import random

try:
    from universe.kospi200 import KOSPI200_CONSTITUENTS
except ImportError:
    from src.universe.kospi200 import KOSPI200_CONSTITUENTS  # type: ignore[no-redef]

_FORCE_INCLUDE = "005930"  # 삼성전자 must always be in pool


def get_pool_codes(
    n: int = 30,
    *,
    sectors: list[str] | None = None,
    seed: int = 42,
) -> list[str]:
    """Return n unique stock codes sampled round-robin across sectors.

    005930 (삼성전자) is always included. Deterministic given same seed.
    """
    constituents = KOSPI200_CONSTITUENTS
    if sectors is not None:
        constituents = [c for c in constituents if c["sector"] in sectors]

    by_sector: dict[str, list[str]] = {}
    for c in constituents:
        by_sector.setdefault(c["sector"], []).append(c["code"])

    rng = random.Random(seed)
    for codes in by_sector.values():
        rng.shuffle(codes)

    sector_order = sorted(by_sector.keys())
    rng.shuffle(sector_order)

    result: list[str] = []
    seen: set[str] = set()

    # Force-include 삼성전자 first (only when not sector-filtered out)
    if _FORCE_INCLUDE in {c["code"] for c in constituents}:
        result.append(_FORCE_INCLUDE)
        seen.add(_FORCE_INCLUDE)

    # Round-robin across sectors
    pointers = {s: 0 for s in sector_order}
    while len(result) < n:
        added_any = False
        for sector in sector_order:
            if len(result) >= n:
                break
            codes = by_sector[sector]
            ptr = pointers[sector]
            while ptr < len(codes) and codes[ptr] in seen:
                ptr += 1
            if ptr < len(codes):
                seen.add(codes[ptr])
                result.append(codes[ptr])
                pointers[sector] = ptr + 1
                added_any = True
        if not added_any:
            break

    return result[:n]


def get_pool(
    n: int = 30,
    *,
    sectors: list[str] | None = None,
    seed: int = 42,
) -> list[dict]:
    """Return n constituent dicts sampled round-robin across sectors."""
    codes = set(get_pool_codes(n, sectors=sectors, seed=seed))
    code_to_entry = {c["code"]: c for c in KOSPI200_CONSTITUENTS}
    return [code_to_entry[code] for code in get_pool_codes(n, sectors=sectors, seed=seed)]

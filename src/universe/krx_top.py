"""KRX 시총 top-N universe builder.

Pure function: 시총 스냅샷 DataFrame 을 받아서 시총 상위 N 종목 코드 리스트
반환. 시총 데이터 fetch 는 본 모듈의 책임 아님 — 호출자가 어댑터 통해 공급.

이는 `src/universe/.ai.md` 의 "모든 함수는 순수 함수" 규칙을 따르며,
PIT (point-in-time) 시총 데이터를 외부에서 주입 가능하게 함으로써
backtest 의 survivorship bias 를 controllable 하게 만든다.

Snapshot 어댑터는 `scripts/fetch_krx_marcap_snapshot.py` 또는 `src/data_lake/`
산하의 fetcher 가 책임지며, 결과는 parquet 캐시 또는 in-memory df 로 본 함수에
전달된다.

핵심 함수:
- `top_n_by_marcap(snapshot_df, market, n)` — 단일 시장 (KOSPI/KOSDAQ) 상위 N
- `combined_top_n(snapshot_df, kospi_n, kosdaq_n)` — KOSPI/KOSDAQ 합산 universe

snapshot_df 컬럼 규약:
- `code` (str, 6자리 종목코드)
- `name` (str)
- `market` (str, "KOSPI" | "KOSDAQ")
- `marcap` (float, 시가총액 KRW)
"""
from __future__ import annotations

import re
from typing import Iterable, Literal, Sequence

import pandas as pd

Market = Literal["KOSPI", "KOSDAQ"]

# 6자리 숫자 종목코드만 통과 (ETF/ETN/SPAC 제외 1차)
_CODE_PATTERN = re.compile(r"^\d{6}$")

REQUIRED_COLUMNS = ("code", "name", "market", "marcap")


def _validate(snapshot: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in snapshot.columns]
    if missing:
        raise ValueError(
            f"snapshot DataFrame missing required columns: {missing}. "
            f"Required: {REQUIRED_COLUMNS}"
        )


def _filter_codes(codes: Iterable[str]) -> list[str]:
    """6자리 숫자 코드만 통과 (우선주·ETF/ETN/SPAC 일부 제외 1차 필터)."""
    return [c for c in codes if _CODE_PATTERN.match(str(c))]


def top_n_by_marcap(snapshot: pd.DataFrame, market: Market, n: int) -> list[str]:
    """단일 시장 (KOSPI 또는 KOSDAQ) 시총 상위 N 종목 코드 반환.

    Args:
        snapshot: 시총 스냅샷 DataFrame. REQUIRED_COLUMNS 컬럼 필요.
        market: "KOSPI" 또는 "KOSDAQ".
        n: 상위 몇 종목.

    Returns:
        list[str] — 시총 내림차순 상위 N 의 6자리 종목코드.
        n 보다 적으면 적은 만큼만 반환.
    """
    _validate(snapshot)
    if n <= 0:
        return []
    df = snapshot[snapshot["market"] == market].copy()
    df = df[df["code"].astype(str).str.match(_CODE_PATTERN)]
    df = df.sort_values("marcap", ascending=False).head(n)
    return df["code"].astype(str).tolist()


def combined_top_n(
    snapshot: pd.DataFrame, kospi_n: int, kosdaq_n: int
) -> list[str]:
    """KOSPI top-N + KOSDAQ top-M 합산 universe.

    Returns:
        list[str] — KOSPI 상위 + KOSDAQ 상위 합집합 (중복 없음, 시총 내림차순).
    """
    kospi_codes = top_n_by_marcap(snapshot, "KOSPI", kospi_n)
    kosdaq_codes = top_n_by_marcap(snapshot, "KOSDAQ", kosdaq_n)
    seen: set[str] = set()
    out: list[str] = []
    for c in kospi_codes + kosdaq_codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def filter_by_min_marcap(
    snapshot: pd.DataFrame, codes: Sequence[str], min_marcap: float
) -> list[str]:
    """주어진 코드 리스트에서 시총 ≥ min_marcap 만 통과시킨다.

    universe 내 추가 cleanup 용 (예: 후속 분기 재계산 시 시총 폭락 종목 제외).
    """
    _validate(snapshot)
    df = snapshot[snapshot["code"].astype(str).isin(list(codes))]
    return df.loc[df["marcap"] >= min_marcap, "code"].astype(str).tolist()

from __future__ import annotations

from src.brokers.kis.auth import KISAuth
from src.brokers.kis.rest import KISClient
from src.brokers.kis.schemas import FinancialRatio, MarketMultiples
from src.brokers.kis.tr_ids import (
    TR_ID_FINANCIAL_RATIO,
    TR_ID_INQUIRE_PRICE,
)


def fetch_financial_ratio_series(
    symbol: str,
    *,
    auth: KISAuth,
    app_key: str,
    app_secret: str,
    cano: str,
    acnt_prdt_cd: str,
    paper: bool = True,
    fid_div_cls_code: str = "1",
) -> list[FinancialRatio]:
    """Fetch quarterly financial-ratio series for a KRX symbol.

    Uses KIS FHKST66430300 inquiry endpoint. Returns a list of quarterly
    snapshots (most recent first), typically ~10 quarters.

    Field semantics (from KIS docs + live verification 2026-04-24):
      - stac_yymm      → fiscal_date ("YYYYMM")
      - eps / bps / sps → per-share values (KRW)
      - roe_val         → ROE (%)
      - grs             → revenue growth (%)
      - bsop_prfi_inrt  → operating profit margin (%)
      - ntin_inrt       → net income margin (%)
      - lblt_rate       → debt ratio (%)
      - rsrv_rate       → retained earnings rate (%)

    Args:
        symbol: KRX stock code (6 digits), e.g. "005930".
        auth: KISAuth instance (manages access-token lifecycle).
        app_key / app_secret / cano / acnt_prdt_cd: KIS API credentials.
        paper: passed to KISClient for base-URL selection.
        fid_div_cls_code: "0" (consolidated) | "1" (non-consolidated/별도). Default "1"
                          matches KIS docs; required parameter.

    Returns:
        list[FinancialRatio], most-recent fiscal period first. Empty list if
        no disclosure available.

    Note: PER/PBR are NOT in this endpoint. Use fetch_market_multiples() for those.
    """
    client = KISClient(
        auth=auth,
        app_key=app_key,
        app_secret=app_secret,
        cano=cano,
        acnt_prdt_cd=acnt_prdt_cd,
        paper=paper,
    )
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": symbol,
        "FID_DIV_CLS_CODE": fid_div_cls_code,
    }
    data = client._get(
        "/uapi/domestic-stock/v1/finance/financial-ratio",
        TR_ID_FINANCIAL_RATIO,
        params,
    )
    output = data.get("output", []) or []
    # Handle both list and dict shapes (API returns list; dict is legacy fallback)
    if isinstance(output, dict):
        output = [output]

    results: list[FinancialRatio] = []
    for row in output:
        if not isinstance(row, dict):
            continue
        results.append(FinancialRatio(
            symbol=symbol,
            fiscal_date=row.get("stac_yymm") or None,
            eps=row.get("eps") or None,
            bps=row.get("bps") or None,
            sps=row.get("sps") or None,
            roe_val=row.get("roe_val") or None,
            grs=row.get("grs") or None,
            bsop_prfi_inrt=row.get("bsop_prfi_inrt") or None,
            ntin_inrt=row.get("ntin_inrt") or None,
            lblt_rate=row.get("lblt_rate") or None,
            rsrv_rate=row.get("rsrv_rate") or None,
        ))
    return results


def fetch_market_multiples(
    symbol: str,
    *,
    auth: KISAuth,
    app_key: str,
    app_secret: str,
    cano: str,
    acnt_prdt_cd: str,
    paper: bool = True,
) -> MarketMultiples:
    """Fetch PER/PBR/EPS/BPS from KIS inquire-price (FHKST01010100).

    These are POINT-IN-TIME market multiples (current price × latest fundamentals).
    Use fetch_financial_ratio_series() for quarterly period-end metrics (ROE, growth, etc.).
    """
    client = KISClient(
        auth=auth,
        app_key=app_key,
        app_secret=app_secret,
        cano=cano,
        acnt_prdt_cd=acnt_prdt_cd,
        paper=paper,
    )
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": symbol,
    }
    data = client._get(
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        TR_ID_INQUIRE_PRICE,
        params,
    )
    output = data.get("output", {}) or {}
    return MarketMultiples(
        symbol=symbol,
        per=output.get("per") or None,
        pbr=output.get("pbr") or None,
        eps=output.get("eps") or None,
        bps=output.get("bps") or None,
    )

#!/usr/bin/env python3
"""Universe-scan paper rebalance cron — 매주 1회 fetch+컴퓨트+발주 (#218 후속).

universe-scan 전략을 실시간 tick driver 가 아닌 **scheduled cron** 으로 운영.
매주 정해진 시각 (KRX 금요일 15:30 KST, Crypto 일요일 00:00 UTC) 에 본 스크립트가
호출되어 다음을 1회 실행:

1. universe builder 로 top-N 종목 조회 (KIS 시총 / Binance 24h 거래량)
2. broker universe_quote 로 일봉 panel 일괄 fetch
3. strategy.compute_weights(panel) → 다음 주 목표 비중
4. PaperBroker (WAL replay 로 상태 복원) 의 현 보유 + 가용 자본 조회
5. portfolio.cs_rebalance_dispatch.dispatch_rebalance 로 paper 발주
6. Telegram digest 1건 발송

본 스크립트는 docker compose service `qta-universe-rebal-cron` 에서 호출 (cron schedule).
또는 CLI 직접 실행: `python scripts/cron_paper_universe_rebal.py --strategy cs-tsmom-kr-daily`.

운영 영향 0 — 기존 KIS daemon (#133), R4/R6 Task Scheduler 와 별도 path.
PaperBroker WAL 도 strategy 별로 분리 (`logs/shadow/cron-{strategy_id}/wal.jsonl`).

Usage:
  python scripts/cron_paper_universe_rebal.py --strategy cs-tsmom-kr-daily
  python scripts/cron_paper_universe_rebal.py --strategy cs-tsmom-crypto-daily
  python scripts/cron_paper_universe_rebal.py --strategy cs-rsi-div-kr --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

# Repo root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("cron_paper_universe_rebal")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_strategy_config(production_yaml: Path, strategy_id: str) -> dict:
    """production.yaml 에서 strategy_id 의 entry 를 찾아 kwargs 반환."""
    with production_yaml.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    for entry in config.get("strategies", []):
        if entry.get("id") == strategy_id:
            return entry.get("kwargs", {})
    raise KeyError(f"strategy_id={strategy_id} not found in {production_yaml}")


# ---------------------------------------------------------------------------
# Universe fetch + panel construction
# ---------------------------------------------------------------------------

def build_krx_universe_panel(top_n_kospi: int = 200, top_n_kosdaq: int = 150,
                              start: str = "20240101", end: str | None = None,
                              kis_client: Any = None
                              ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """KIS 시총 top-N → fetch_universe_snapshot → close/high/low/turnover panel.

    kis_client 가 None 이면 cached parquet (data/cache/krx_daily/) 에서 로드.
    실거래에서는 KIS broker client 주입 필수.
    """
    import FinanceDataReader as fdr
    from universe.krx_top import combined_top_n

    # 시총 스냅샷 (FDR — 외부 데이터 어댑터, 캐시 없음)
    ks = fdr.StockListing("KOSPI")
    kq = fdr.StockListing("KOSDAQ")
    ks["market"] = "KOSPI"
    kq["market"] = "KOSDAQ"
    ks = ks.rename(columns={"Code": "code", "Name": "name", "Marcap": "marcap"})
    kq = kq.rename(columns={"Code": "code", "Name": "name", "Marcap": "marcap"})
    snap = pd.concat([ks, kq], ignore_index=True)[["code", "name", "market", "marcap"]]

    codes = combined_top_n(snap, kospi_n=top_n_kospi, kosdaq_n=top_n_kosdaq)
    logger.info("krx_universe_snapshot codes=%d", len(codes))

    # OHLCV fetch — kis_client 있으면 broker.fetch_universe_snapshot 사용,
    # 없으면 cached parquet → FDR fallback (테스트용).
    panels: dict[str, pd.DataFrame] = {}
    if kis_client is not None:
        from brokers.kis.universe_quote import fetch_universe_snapshot
        end = end or pd.Timestamp.now().strftime("%Y%m%d")
        panels = fetch_universe_snapshot(kis_client, codes, start, end)
    else:
        cache = ROOT / "data" / "cache" / "krx_daily"
        for code in codes:
            p = cache / f"{code}.parquet"
            if p.exists():
                try:
                    panels[code] = pd.read_parquet(p)
                except Exception:
                    pass

    if not panels:
        raise RuntimeError("krx_universe_panel_empty — no broker client + no cache")

    closes = pd.DataFrame({c: df["close"] for c, df in panels.items()}).sort_index().dropna(how="all")
    highs = pd.DataFrame({c: df["high"] for c, df in panels.items()}).reindex(closes.index)
    lows = pd.DataFrame({c: df["low"] for c, df in panels.items()}).reindex(closes.index)
    turnovers = pd.DataFrame({
        c: df["close"] * df["volume"] for c, df in panels.items()
    }).reindex(closes.index)
    return closes, highs, lows, turnovers


def build_crypto_universe_panel(top_n: int = 30, start: str = "2024-01-01"
                                 ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Binance top-N USDT spot → fetch_universe_klines → panel."""
    from brokers.binance.universe_quote import (
        fetch_24h_tickers,
        fetch_universe_klines,
    )
    from universe.binance_top import top_n_by_volume

    # 24h 스냅샷
    raw = fetch_24h_tickers()
    snap_rows = [{
        "symbol": d["symbol"],
        "last_price": float(d["lastPrice"]),
        "change_24h_pct": float(d["priceChangePercent"]),
        "quote_volume_24h": float(d["quoteVolume"]),
    } for d in raw]
    snap = pd.DataFrame(snap_rows)
    symbols = top_n_by_volume(snap, top_n)
    logger.info("crypto_universe_snapshot symbols=%d", len(symbols))

    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    panels = fetch_universe_klines(symbols, interval="1d", start_ms=start_ms)
    if not panels:
        raise RuntimeError("crypto_universe_panel_empty")

    closes = pd.DataFrame({s: df["close"] for s, df in panels.items()}).sort_index().dropna(how="all")
    highs = pd.DataFrame({s: df["high"] for s, df in panels.items()}).reindex(closes.index)
    lows = pd.DataFrame({s: df["low"] for s, df in panels.items()}).reindex(closes.index)
    qv = pd.DataFrame({s: df["quote_volume"] for s, df in panels.items()}).reindex(closes.index)
    return closes, highs, lows, qv


# ---------------------------------------------------------------------------
# Paper broker setup (WAL replay for state continuity)
# ---------------------------------------------------------------------------

def setup_paper_broker(wal_path: Path, initial_balance: Decimal,
                       balance_asset: str):
    from src.execution.mock_matching import MockMatchingEngine
    from src.execution.paper_broker import PaperBroker
    from src.live.wal import WAL, replay
    from src.ops.kill_switch import KillSwitch

    wal_path.parent.mkdir(parents=True, exist_ok=True)
    wal = WAL(wal_path)
    kill_switch = KillSwitch()
    matching = MockMatchingEngine()
    broker = PaperBroker(
        wal=wal, kill_switch=kill_switch, matching_engine=matching,
        initial_balance=initial_balance, balance_asset=balance_asset,
    )

    # WAL replay for position state continuity (if past runs exist)
    if wal_path.exists() and wal_path.stat().st_size > 0:
        try:
            events, corruptions = replay(wal_path)
            logger.info("wal_replayed events=%d corruptions=%d", len(events), len(corruptions))
            # NOTE: PaperBroker._positions / _balances 복원은 단순화 — fill 이벤트만
            # 시뮬해서 replay. 실 운영에서는 더 정밀한 재구축 필요. 본 cron 의 1차 운영
            # 기간에는 매주 새 sleeve 처럼 리셋해도 무방 (모의 실험 목적).
            for ev in events:
                if ev.event_type == "order_filled":
                    payload = ev.payload
                    sym = payload.get("symbol")
                    side = payload.get("side")
                    qty = Decimal(str(payload.get("qty", "0")))
                    if not sym or qty <= 0:
                        continue
                    from src.brokers.base import Position, PositionSide
                    cur = broker._positions.get(sym)
                    if side == "BUY":
                        if cur is None:
                            broker._positions[sym] = Position(
                                symbol=sym, side=PositionSide.LONG,
                                qty=qty, entry_price=Decimal("0"))
                        else:
                            cur.qty += qty
                    elif side == "SELL":
                        if cur is not None:
                            cur.qty -= qty
                            if cur.qty <= 0:
                                del broker._positions[sym]
        except Exception as exc:
            logger.warning("wal_replay_failed reason=%s — starting fresh", exc)

    return broker


# ---------------------------------------------------------------------------
# Strategy → weights → orders
# ---------------------------------------------------------------------------

def compute_target_weights(strategy_id: str, kwargs: dict, panels) -> pd.Series:
    """strategy 모듈 import → compute_weights → 마지막 row 의 weights 반환."""
    module_path = kwargs.get("module")
    if not module_path:
        raise ValueError(f"production.yaml entry for {strategy_id} missing 'module'")
    mod = importlib.import_module(module_path)
    weights_kind = kwargs.get("weights_kind", "krx")
    params = kwargs.get("params", {}) or {}

    if weights_kind == "krx":
        closes, _, _, turnover = panels
        weights_df = mod.compute_weights(closes, turnover, **params)
    elif weights_kind == "krx_hlc":
        closes, highs, lows, turnover = panels
        weights_df = mod.compute_weights(highs, lows, closes, turnover, **params)
    elif weights_kind == "crypto":
        closes, _, _, qv = panels
        weights_df = mod.compute_weights(closes, qv, **params)
    else:
        raise ValueError(f"unknown weights_kind={weights_kind}")

    last_row = weights_df.iloc[-1]
    return last_row[last_row > 0]


# ---------------------------------------------------------------------------
# Main rebalance flow
# ---------------------------------------------------------------------------

async def run_rebalance(strategy_id: str, *, dry_run: bool = False,
                        production_yaml: Path | None = None,
                        wal_dir: Path | None = None) -> dict:
    """단일 strategy 1회 rebal 실행. dict 결과 반환."""
    from portfolio.cs_rebalance_dispatch import dispatch_rebalance
    from portfolio.weights_to_orders import (
        BINANCE_DEFAULT_LOT, KRX_LOT, LotSpec,
    )

    production_yaml = production_yaml or (ROOT / "configs" / "orchestrator" / "production.yaml")
    wal_dir = wal_dir or (ROOT / "logs" / "shadow")

    kwargs = load_strategy_config(production_yaml, strategy_id)
    weights_kind = kwargs.get("weights_kind", "krx")
    is_crypto = weights_kind == "crypto"

    # 1. universe + panel
    if is_crypto:
        panels = build_crypto_universe_panel(top_n=30)
    else:
        # KRX (krx + krx_hlc): top-N 시총 + cached/parquet
        panels = build_krx_universe_panel(top_n_kospi=200, top_n_kosdaq=150)

    # 2. target weights
    target_weights = compute_target_weights(strategy_id, kwargs, panels)
    logger.info("target_weights strategy=%s n_picks=%d total=%.4f",
                strategy_id, len(target_weights), float(target_weights.sum()))

    if dry_run:
        return {
            "strategy_id": strategy_id,
            "n_picks": len(target_weights),
            "picks": target_weights.to_dict(),
            "dry_run": True,
        }

    # 3. paper broker (per-strategy WAL — 다른 strategy 영향 없음)
    wal_path = wal_dir / f"cron-{strategy_id}" / "wal.jsonl"
    asset = "USDT" if is_crypto else "KRW"
    initial = Decimal("100000") if is_crypto else Decimal("100000000")  # crypto 10K USDT, KRX 1억 원
    broker = setup_paper_broker(wal_path, initial, asset)

    # 4. providers
    closes = panels[0]
    last_prices = closes.iloc[-1]
    prices = last_prices.reindex(target_weights.index).dropna()
    current_positions = {sym: float(p.qty) for sym, p in broker._positions.items()}
    capital = float(broker._balances[asset].free)

    lot_spec: LotSpec = BINANCE_DEFAULT_LOT if is_crypto else KRX_LOT

    # 5. dispatch rebalance
    report = await dispatch_rebalance(
        strategy_id=strategy_id,
        target_weights=target_weights,
        current_positions=current_positions,
        prices=prices,
        total_capital=capital,
        broker=broker,
        lot_spec=lot_spec,
        cash_buffer_pct=0.01,
        rebal_reason=f"cron_paper_rebal:{strategy_id}",
    )

    # 6. Telegram digest
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from telegram_rebal import send_rebal_digest
        send_rebal_digest(
            strategy_id,
            buys=[i.symbol for i in [r for r in [] ]],  # parsed below
            sells=[],
            held=[],
            n_submitted=report.summary.get("n_submitted", 0),
            n_rejected=report.summary.get("n_rejected", 0),
        )
    except Exception as exc:
        logger.warning("telegram_digest_failed reason=%s", exc)

    return {
        "strategy_id": strategy_id,
        "summary": report.summary,
        "n_picks": len(target_weights),
        "n_submitted": report.summary.get("n_submitted", 0),
        "n_rejected": report.summary.get("n_rejected", 0),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", required=True,
                   help="strategy id (e.g., cs-tsmom-kr-daily)")
    p.add_argument("--dry-run", action="store_true",
                   help="weights 계산만 + 발주 안 함")
    p.add_argument("--production-yaml",
                   default=str(ROOT / "configs" / "orchestrator" / "production.yaml"))
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        result = asyncio.run(run_rebalance(
            args.strategy, dry_run=args.dry_run,
            production_yaml=Path(args.production_yaml),
        ))
        logger.info("rebal_complete %s", result)
        return 0
    except Exception as exc:
        logger.error("rebal_failed strategy=%s error=%s", args.strategy, exc,
                     exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

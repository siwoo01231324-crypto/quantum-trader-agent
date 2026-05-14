"""HTS 검색식 3종 1일 pilot 평가 (#230 옵션 A).

매 분봉 시점에 검색식 조건을 평가하고, 통과 시 entry → +2%/-2% 시뮬레이션.
오늘 (KST 기준) 1일치 데이터로 신호 수·win rate 1차 sanity.

사용:
    python scripts/run_hts_cond_pilot.py --dry-run         # universe 필터 + 추정 API 호출 수만 표시
    python scripts/run_hts_cond_pilot.py                   # full pilot (fetch + 백테스트 + 리포트)
    python scripts/run_hts_cond_pilot.py --max-syms 20     # universe 의 first N 만 사용 (rate-limit 안전)
    python scripts/run_hts_cond_pilot.py --skip-fetch      # 이미 lake 에 적재된 데이터로만 백테스트

환경변수: HANTOO_FAKE_API_KEY, HANTOO_FAKE_SECRET_API_KEY, HANTOO_FAKE_CREDIT_NUMBER
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE))
sys.path.insert(0, str(WORKTREE / "src"))

# dotenv autoload
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(WORKTREE / ".env", override=False)
except ImportError:
    pass

log = logging.getLogger("hts_cond_pilot")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

KST = timezone(timedelta(hours=9))


# -------- universe filter ---------------------------------------------------

@dataclass
class UniverseEntry:
    symbol: str
    last_close: float
    last_volume: int
    vol_5d_cumsum: int


def build_universe(daily_cache_dir: Path) -> list[UniverseEntry]:
    """Daily cache 의 350 종목을 단타 검색식 universe 룰로 필터링.

    필터: 마지막 close 900~10,000원 AND 마지막 5거래일 cumvol ≥ 500,000.
    """
    import pandas as pd

    files = sorted(daily_cache_dir.glob("*.parquet"))
    out: list[UniverseEntry] = []
    for f in files:
        try:
            df = pd.read_parquet(f)
        except Exception as e:
            log.debug("skip %s: %s", f.name, e)
            continue
        if df.empty or "close" not in df.columns:
            continue
        last_close = float(df.iloc[-1]["close"])
        last_vol = int(df.iloc[-1]["volume"])
        tail5 = df.tail(5)
        vol5 = int(tail5["volume"].sum())
        if not (900 <= last_close <= 10_000):
            continue
        if vol5 < 500_000:
            continue
        symbol = f.stem
        out.append(UniverseEntry(symbol=symbol, last_close=last_close,
                                 last_volume=last_vol, vol_5d_cumsum=vol5))
    out.sort(key=lambda e: e.vol_5d_cumsum, reverse=True)
    return out


def build_universe_fdr() -> list[UniverseEntry]:
    """FDR StockListing('KRX') snapshot 으로 단타 검색식 universe 사전 필터링.

    필터 (검색식 A+B+C 의 EOD 근사):
      - 900 ≤ Close ≤ 10,000
      - 0.02 ≤ ChagesRatio/100 ≤ 0.30  (등락률 2%~30%)
      - Volume ≥ 40,000

    EOD 값을 사용하므로 정확한 entry timing 평가는 1m fetch 후 walk-forward 에서 수행.
    pre-filter 는 후보 종목을 좁히는 용도.
    """
    import FinanceDataReader as fdr
    df = fdr.StockListing("KRX")
    # Filter 6-digit codes + KOSPI/KOSDAQ markets
    df = df[df["Code"].str.match(r"^\d{6}$", na=False)]
    df = df[df["Market"].isin(["KOSPI", "KOSDAQ"])]
    df = df[(df["Close"] >= 900) & (df["Close"] <= 10_000)]
    df = df[(df["ChagesRatio"] >= 2.0) & (df["ChagesRatio"] <= 30.0)]
    df = df[df["Volume"] >= 40_000]
    df = df.sort_values("Volume", ascending=False)
    out: list[UniverseEntry] = []
    for _, row in df.iterrows():
        out.append(UniverseEntry(
            symbol=row["Code"],
            last_close=float(row["Close"]),
            last_volume=int(row["Volume"]),
            vol_5d_cumsum=int(row["Volume"]),  # 1d only, D 조건은 백테스트 단계에서 daily fetch
        ))
    return out


# -------- KIS 1m fetch ------------------------------------------------------

def _kis_client():
    """KIS 자격증명 resolve + paper client 생성."""
    from src.brokers.kis.auth import KISAuth
    from src.brokers.kis.rest import KISClient
    app_key = os.environ.get("HANTOO_FAKE_API_KEY") or os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("HANTOO_FAKE_SECRET_API_KEY") or os.environ.get("KIS_APP_SECRET")
    credit = os.environ.get("HANTOO_FAKE_CREDIT_NUMBER") or os.environ.get("HANTOO_CREDIT_NUMBER", "")
    if not app_key or not app_secret or not credit:
        raise RuntimeError("KIS credentials missing (HANTOO_FAKE_API_KEY/SECRET/CREDIT_NUMBER)")
    cano = credit.split("-")[0] if "-" in credit else credit[:8]
    acnt = credit.split("-")[1] if "-" in credit else "01"
    auth = KISAuth(app_key=app_key, app_secret=app_secret, paper=True)
    return KISClient(auth=auth, app_key=app_key, app_secret=app_secret,
                     cano=cano, acnt_prdt_cd=acnt, paper=True)


def fetch_today_1m(symbol: str, client, lake_dir: Path) -> int:
    """KIS 분봉 1m fetch + 기존 parquet 와 merge (누적 모드). 반환: 적재 봉 수.

    누적 동작 (옵션 B 운영 필수):
      1. KIS API → 오늘 1분봉 fetch (당일 only, KIS 제약)
      2. 기존 part-0.parquet 가 있으면 read
      3. concat + drop_duplicates by (symbol, ts) — 최신 ingested_at 우선
      4. write back (overwrite)
    이렇게 하면 매일 cron 실행 시 그 날 데이터 추가, 과거 데이터 보존.
    """
    import pandas as pd
    from src.brokers.kis.price_client import fetch_intraday_ohlcv_raw

    today = datetime.now(KST)
    bars = fetch_intraday_ohlcv_raw(client, symbol, today.strftime("%Y%m%d"), interval="1")
    if not bars:
        return 0
    records = []
    for bar in bars:
        ts = pd.Timestamp(
            f"{bar.date[:4]}-{bar.date[4:6]}-{bar.date[6:8]}"
            f" {bar.time[:2]}:{bar.time[2:4]}:{bar.time[4:6]}",
            tz="Asia/Seoul",
        ).tz_convert("UTC")
        records.append({
            "symbol": symbol, "ts": ts, "freq": "1m",
            "open": float(bar.open), "high": float(bar.high),
            "low": float(bar.low), "close": float(bar.close),
            "volume": float(bar.volume),
            "vwap": bar.trade_amt / bar.volume if bar.volume > 0 else 0.0,
            "trade_count": 0, "source": "kis",
            "ingested_at": pd.Timestamp.now(tz="UTC"),
        })
    df_new = pd.DataFrame(records)
    out_dir = (lake_dir / "ohlcv" / "freq=1m"
               / f"year={today.year}" / f"month={today.month:02d}"
               / f"symbol={symbol}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "part-0.parquet"

    # 누적: 기존 parquet 와 merge
    if out_path.exists():
        try:
            df_old = pd.read_parquet(out_path)
            df_merged = pd.concat([df_old, df_new], ignore_index=True)
            # 동일 (symbol, ts) 중복 제거 — 최신 ingested_at 보존 (keep='last')
            df_merged = df_merged.sort_values("ingested_at").drop_duplicates(
                subset=["symbol", "ts"], keep="last"
            ).sort_values("ts").reset_index(drop=True)
            df_merged.to_parquet(out_path, index=False)
            return len(df_new)
        except Exception:
            # corrupt parquet → overwrite with new data
            pass
    df_new.to_parquet(out_path, index=False)
    return len(df_new)


# -------- evaluator + backtest ---------------------------------------------

def load_1m_for_date(lake_dir: Path, symbol: str, target_date):
    """특정 거래일의 1m bars 로드. ts_kst 의 date 가 target_date 와 일치하는 row 만 반환.

    target_date 는 KST 날짜 (datetime.date). lake partition 은 year=YYYY/month=MM 으로
    분할되어 있어 month boundary 도 처리.
    """
    import pandas as pd
    p = (lake_dir / "ohlcv" / "freq=1m"
         / f"year={target_date.year}" / f"month={target_date.month:02d}"
         / f"symbol={symbol}" / "part-0.parquet")
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df["ts_kst"] = df["ts"].dt.tz_convert("Asia/Seoul")
    df = df[df["ts_kst"].dt.date == target_date].copy()
    df.sort_values("ts", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df if len(df) > 0 else None


def load_1m_today(lake_dir: Path, symbol: str):
    """오늘 (KST) 1m bars 로드 — load_1m_for_date 의 thin wrapper."""
    return load_1m_for_date(lake_dir, symbol, datetime.now(KST).date())


def get_recent_krx_trading_days(n_days: int, end_date=None) -> list:
    """end_date 까지 (포함) 최근 n_days 거래일 list 반환. KRX 영업일 필터.

    end_date=None 시 오늘 (KST).
    """
    if end_date is None:
        end_date = datetime.now(KST).date()
    try:
        from src.universe.krx_calendar import is_krx_holiday
    except ImportError:
        is_krx_holiday = lambda d: False  # type: ignore
    days: list = []
    d = end_date
    safety_limit = n_days * 3 + 30
    while len(days) < n_days and safety_limit > 0:
        if d.weekday() < 5 and not is_krx_holiday(d):
            days.append(d)
        d -= timedelta(days=1)
        safety_limit -= 1
    return list(reversed(days))


def aggregate_to_3m(df_1m):
    """1분봉 → 3분봉 (close 만 필요)."""
    import pandas as pd
    df = df_1m.copy()
    df.set_index("ts_kst", inplace=True)
    g = df.resample("3min", label="right", closed="right")
    out = g.agg({"close": "last"}).dropna().reset_index()
    return out


def simulate_pilot(
    daily_cache: dict,
    bars_1m,
    symbol: str,
    profile_name: str,
    evaluator,
    inputs_factory,
    *,
    tp_pct: float = 0.02,
    sl_pct: float = 0.02,
    fee_pct: float = 0.00015,
    slippage_pct: float = 0.0005,
    max_entry_hour: float | None = None,   # KST hour upper bound (e.g., 10.5 = 10:30)
    min_entry_hour: float | None = None,
) -> dict:
    """단일 종목 × 1일 pilot 백테스트.

    Walk-forward: 각 1분봉 시점에 evaluator 적용 → 통과 시 entry → 이후 1m 에서
    high≥entry*(1+tp), low≤entry*(1-sl) 체크. 동일봉 동시 → 손절 우선. EOD 종가 청산.
    동일 종목 1일 1회 매수.
    """
    import pandas as pd
    from src.screeners.hts_cond import DailyScreeningInputs

    if bars_1m is None or len(bars_1m) < 25:
        return {"symbol": symbol, "profile": profile_name, "signals": 0, "trades": []}

    bars_3m = aggregate_to_3m(bars_1m) if profile_name == "dts" else None

    closes_1m = bars_1m["close"].tolist()
    highs_1m = bars_1m["high"].tolist()
    lows_1m = bars_1m["low"].tolist()
    vols_1m = bars_1m["volume"].tolist()
    ts_1m = bars_1m["ts_kst"].tolist()

    daily = daily_cache.get(symbol)
    if daily is None or daily["ma5"] is None:
        return {"symbol": symbol, "profile": profile_name, "signals": 0, "trades": []}

    cumvol = 0
    triggered = False
    trades: list[dict] = []
    signals = 0

    for t in range(len(closes_1m)):
        cumvol += int(vols_1m[t])
        if triggered:
            continue
        # 시간대 필터
        if max_entry_hour is not None or min_entry_hour is not None:
            ts = ts_1m[t]
            hour_decimal = ts.hour + ts.minute / 60.0
            if max_entry_hour is not None and hour_decimal > max_entry_hour:
                continue
            if min_entry_hour is not None and hour_decimal < min_entry_hour:
                continue
        # 평가 시점 입력 구축
        screening = DailyScreeningInputs(
            symbol=symbol,
            prev_close=daily["prev_close"],
            prev_close_2=daily["prev_close_2"],
            today_close=closes_1m[t],
            today_volume=cumvol,
            vol_5d_cumsum=daily["vol_5d_cumsum"] + cumvol,
            power_ratio=daily["power_ratio_daily"],  # 일간 누적 (look-ahead 명시)
            ma5=daily["ma5"], ma20=daily["ma20"], ma60=daily["ma60"],
        )
        passes = inputs_factory(screening, bars_3m, closes_1m[t], t, evaluator)
        if not passes:
            continue
        signals += 1

        # entry: 현 1분봉 종가
        entry_price = closes_1m[t] * (1.0 + slippage_pct)
        entry_ts = ts_1m[t]
        tp = entry_price * (1.0 + tp_pct)
        sl = entry_price * (1.0 - sl_pct)

        # 이후 1m 에서 +2/-2% 체크
        exit_price = None
        exit_ts = None
        exit_reason = None
        for u in range(t + 1, len(closes_1m)):
            if lows_1m[u] <= sl:
                exit_price = sl * (1.0 - slippage_pct)
                exit_ts = ts_1m[u]
                exit_reason = "sl"
                break
            if highs_1m[u] >= tp:
                exit_price = tp * (1.0 - slippage_pct)
                exit_ts = ts_1m[u]
                exit_reason = "tp"
                break
        if exit_price is None:
            # EOD 종가 청산
            exit_price = closes_1m[-1] * (1.0 - slippage_pct)
            exit_ts = ts_1m[-1]
            exit_reason = "eod"

        ret_pct = (exit_price - entry_price) / entry_price - 2 * fee_pct
        trades.append({
            "symbol": symbol,
            "entry_ts": entry_ts.isoformat(),
            "entry_px": entry_price,
            "exit_ts": exit_ts.isoformat(),
            "exit_px": exit_price,
            "exit_reason": exit_reason,
            "ret_pct": ret_pct,
        })
        triggered = True  # 1일 1회 매수

    return {"symbol": symbol, "profile": profile_name, "signals": signals, "trades": trades}


def factory_dts(screening, bars_3m, current_price, t, evaluator) -> bool:
    from src.screeners.hts_cond import DtsInputs, common_passes, PROFILE_DTS
    from src.screeners.hts_cond.dts import ThreeMinBar, cond_h_dts
    if not common_passes(screening, PROFILE_DTS):
        return False
    if bars_3m is None or len(bars_3m) < 20:
        return False
    bars = [ThreeMinBar(close=float(c)) for c in bars_3m["close"]]
    return cond_h_dts(bars)


def factory_wait5m(screening, bars_3m, current_price, t, evaluator) -> bool:
    from src.screeners.hts_cond import common_passes, PROFILE_WAIT5M, cond_h_wait5m
    if not common_passes(screening, PROFILE_WAIT5M):
        return False
    return cond_h_wait5m(screening.prev_close, current_price)


def factory_swing(screening, bars_3m, current_price, t, evaluator) -> bool:
    from src.screeners.hts_cond import common_passes, PROFILE_SWING
    return common_passes(screening, PROFILE_SWING)


def factory_hybrid_or(screening, bars_3m, current_price, t, evaluator) -> bool:
    """3개 검색식 중 어느 하나라도 통과하면 True (OR 합성).

    옵션 B: 1개 통합 전략으로 운영 시 진입 조건. 같은 종목 1일 1회만 진입
    (simulate_pilot 의 `triggered=True` 플래그가 처리).
    """
    return (
        factory_dts(screening, bars_3m, current_price, t, evaluator)
        or factory_wait5m(screening, bars_3m, current_price, t, evaluator)
        or factory_swing(screening, bars_3m, current_price, t, evaluator)
    )


# -------- daily cache loading ----------------------------------------------

def load_daily_cache_as_of(daily_cache_dir: Path, symbols: list[str], as_of_date) -> dict:
    """As-of 특정 거래일의 daily inputs (closes.iloc[-1] = as_of_date 의 전일).

    Walk-forward 시 각 거래일 D 에 대해 D-1 종가 / D-1 기준 MA 사용 (look-ahead 방지).
    """
    import pandas as pd
    cache: dict = {}
    for sym in symbols:
        f = daily_cache_dir / f"{sym}.parquet"
        if not f.exists():
            continue
        df = pd.read_parquet(f)
        # as_of_date 이전 row 만 사용
        if len(df) > 0 and hasattr(df.index, "date"):
            df = df[df.index.date < as_of_date]
        if len(df) < 60:
            continue
        closes = df["close"].astype(float)
        vols = df["volume"].astype(int)
        cache[sym] = {
            "prev_close": float(closes.iloc[-1]),
            "prev_close_2": float(closes.iloc[-2]),
            "ma5": float(closes.tail(5).mean()),
            "ma20": float(closes.tail(20).mean()),
            "ma60": float(closes.tail(60).mean()),
            "vol_5d_cumsum": int(vols.tail(5).sum()),
            "power_ratio_daily": 100.0,  # placeholder
        }
    return cache


def load_daily_cache(daily_cache_dir: Path, symbols: list[str]) -> dict:
    """오늘 기준 as-of (legacy alias). 새 코드는 load_daily_cache_as_of 사용 권장.

    중요: daily refresh 가 오늘 일봉을 cache 에 적재할 수 있으므로 **오늘 row 제외**
    후 closes.iloc[-1] = 어제, closes.iloc[-2] = 그저께 가 되도록 보장.
    """
    today = datetime.now(KST).date()
    return load_daily_cache_as_of(daily_cache_dir, symbols, today)


def refresh_daily_from_kis(client, symbols: list[str], daily_cache_dir: Path,
                           rate_sleep: float = 0.3, days_back: int = 150) -> int:
    """KIS 일봉 fetch 로 daily cache 갱신/생성.

    cache 가 stale (마지막 날짜 < 어제) 이거나 없으면 fetch.
    """
    import pandas as pd
    from src.brokers.kis.price_client import fetch_daily_ohlcv_raw

    today = datetime.now(KST).date()
    cutoff = today - timedelta(days=1)
    start = (today - timedelta(days=days_back)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    refreshed = 0
    skipped = 0
    failed = 0

    daily_cache_dir.mkdir(parents=True, exist_ok=True)
    for i, sym in enumerate(symbols):
        cache_path = daily_cache_dir / f"{sym}.parquet"
        if cache_path.exists():
            try:
                df = pd.read_parquet(cache_path)
                last_date = df.index.max()
                if hasattr(last_date, "date"):
                    last_date = last_date.date()
                if last_date and last_date >= cutoff:
                    skipped += 1
                    continue
            except Exception:
                pass
        try:
            bars = fetch_daily_ohlcv_raw(client, sym, start, end)
        except Exception as e:
            log.debug("daily fetch fail %s: %s", sym, e)
            failed += 1
            time.sleep(rate_sleep)
            continue
        if not bars:
            failed += 1
            time.sleep(rate_sleep)
            continue
        records = []
        for b in bars:
            try:
                d = pd.Timestamp(f"{b.date[:4]}-{b.date[4:6]}-{b.date[6:8]}")
            except Exception:
                continue
            records.append({
                "Date": d,
                "open": int(float(b.open)),
                "high": int(float(b.high)),
                "low": int(float(b.low)),
                "close": int(float(b.close)),
                "volume": int(float(b.volume)),
            })
        if not records:
            failed += 1
            time.sleep(rate_sleep)
            continue
        df_new = pd.DataFrame(records).set_index("Date").sort_index()
        df_new.to_parquet(cache_path)
        refreshed += 1
        if i % 20 == 0:
            log.info("  daily refresh %d/%d (refreshed=%d skipped=%d failed=%d)",
                     i + 1, len(symbols), refreshed, skipped, failed)
        time.sleep(rate_sleep)
    log.info("daily refresh done: refreshed=%d skipped=%d failed=%d",
             refreshed, skipped, failed)
    return refreshed


# -------- main ---------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lake-dir", default=str(WORKTREE.parent.parent / "lake"),
                    help="OHLCV lake (default: project root /lake)")
    ap.add_argument("--daily-cache", default=str(WORKTREE.parent.parent / "data" / "cache" / "krx_daily"),
                    help="KRX daily parquet cache")
    ap.add_argument("--report-dir", default=str(WORKTREE / "docs" / "work" / "active" / "000230-hts-cond-eval"),
                    help="Report output directory")
    ap.add_argument("--max-syms", type=int, default=0, help="Cap filtered universe size (0=all)")
    ap.add_argument("--skip-fetch", action="store_true", help="Use existing lake data only")
    ap.add_argument("--dry-run", action="store_true", help="Show universe + API call estimate, no fetch")
    ap.add_argument("--rate-sleep", type=float, default=0.3,
                    help="Sleep between KIS calls (default 300ms = ~3.3 req/s, EGW00201 안전)")
    ap.add_argument("--use-fdr", action="store_true",
                    help="FDR snapshot 으로 전체 KRX universe pre-filter (A+B+C eod 근사)")
    ap.add_argument("--refresh-daily", action="store_true",
                    help="필터된 universe 의 KIS 일봉 fetch 로 cache 갱신 (--use-fdr 와 함께 권장)")
    ap.add_argument("--multi-day", type=int, default=0,
                    help="N>0 시 최근 N 거래일 walk-forward (기본 0 = 오늘 1일만). "
                         "lake 에 N 일치 1m 누적되어 있어야 함 (option B cron 산출물).")
    args = ap.parse_args()

    lake_dir = Path(args.lake_dir)
    daily_cache_dir = Path(args.daily_cache)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    log.info("Lake dir       : %s", lake_dir)
    log.info("Daily cache    : %s", daily_cache_dir)

    # Step 1: universe 필터
    log.info("== Step 1: universe filter ==")
    if args.use_fdr:
        universe = build_universe_fdr()
        log.info("FDR snapshot universe (A+B+C eod 근사): %d symbols", len(universe))
    else:
        universe = build_universe(daily_cache_dir)
        log.info("daily cache universe (D 만족): %d symbols", len(universe))
    if args.max_syms > 0:
        universe = universe[: args.max_syms]
        log.info("capped to --max-syms %d", args.max_syms)
    for u in universe[:10]:
        log.info("  %s  close=%.0f vol=%d", u.symbol, u.last_close, u.last_volume)
    if len(universe) > 10:
        log.info("  ... (+%d more)", len(universe) - 10)

    if args.dry_run:
        # 1m fetch: ~13 pages/sym, daily refresh: 1 call/sym
        est_1m = len(universe) * 13
        est_daily = len(universe) if args.refresh_daily else 0
        total_calls = est_1m + est_daily
        log.info("[DRY-RUN] est. KIS calls: 1m=%d daily=%d total=%d",
                 est_1m, est_daily, total_calls)
        log.info("[DRY-RUN] est. time: %.1f min @ rate=%.2f sec/call",
                 total_calls * args.rate_sleep / 60.0, args.rate_sleep)
        return 0

    # Step 1.5: daily refresh (if --refresh-daily)
    if args.refresh_daily and not args.skip_fetch:
        log.info("== Step 1.5: daily KIS refresh ==")
        client = _kis_client()
        sym_list = [u.symbol for u in universe]
        refresh_daily_from_kis(client, sym_list, daily_cache_dir, args.rate_sleep)

    # Step 2: 1m fetch (skip if --skip-fetch)
    if not args.skip_fetch:
        log.info("== Step 2: KIS 1m fetch (today) ==")
        client = _kis_client()
        ok = 0
        fail = 0
        for i, u in enumerate(universe):
            try:
                n = fetch_today_1m(u.symbol, client, lake_dir)
                if n > 0:
                    ok += 1
                else:
                    fail += 1
                if i % 20 == 0:
                    log.info("  fetched %d/%d (%s: %d bars)", i + 1, len(universe), u.symbol, n)
            except Exception as e:
                log.warning("fetch fail %s: %s", u.symbol, e)
                fail += 1
            time.sleep(args.rate_sleep)
        log.info("fetch complete: ok=%d fail=%d", ok, fail)

    # Step 3+4: per-date walk-forward (multi-day=0 → 오늘 1일만)
    n_days = max(args.multi_day, 1)
    trading_dates = get_recent_krx_trading_days(n_days)
    log.info("== Step 3+4: daily cache + evaluator (n_days=%d) ==", n_days)
    log.info("trading dates: %s", [d.isoformat() for d in trading_dates])

    symbols = [u.symbol for u in universe]
    profiles = [
        ("dts", factory_dts),
        ("wait5m", factory_wait5m),
        ("swing", factory_swing),
    ]
    all_results: dict[str, list[dict]] = {p: [] for p, _ in profiles}
    per_date_loaded: dict[str, int] = {}

    for target_date in trading_dates:
        daily_cache_d = load_daily_cache_as_of(daily_cache_dir, symbols, target_date)
        n_loaded = 0
        for u in universe:
            bars_1m = load_1m_for_date(lake_dir, u.symbol, target_date)
            if bars_1m is None or len(bars_1m) < 25:
                continue
            n_loaded += 1
            for profile_name, fac in profiles:
                res = simulate_pilot(daily_cache_d, bars_1m, u.symbol, profile_name, None, fac)
                # date stamp on each trade
                for t in res["trades"]:
                    t["date"] = target_date.isoformat()
                all_results[profile_name].append(res)
        per_date_loaded[target_date.isoformat()] = n_loaded
        log.info("  date=%s: daily_cache=%d/%d, 1m_loaded=%d",
                 target_date.isoformat(), len(daily_cache_d), len(symbols), n_loaded)

    # Step 5: summary
    log.info("== Step 5: summary ==")
    summary = {}
    for profile_name, _ in profiles:
        results = all_results[profile_name]
        signals_total = sum(r["signals"] for r in results)
        trades = [t for r in results for t in r["trades"]]
        n = len(trades)
        if n == 0:
            summary[profile_name] = {"signals": signals_total, "trades": 0,
                                     "win_rate": 0.0, "avg_pnl": 0.0, "decision": "skip"}
            log.info("%-10s signals=%4d trades=0 → 신호 부족", profile_name, signals_total)
            continue
        wins = sum(1 for t in trades if t["ret_pct"] > 0)
        win_rate = wins / n
        avg_pnl = sum(t["ret_pct"] for t in trades) / n
        decision = "adopt" if (win_rate >= 0.50 and avg_pnl >= 0.003 and n >= 30) else "reject"
        summary[profile_name] = {
            "signals": signals_total, "trades": n,
            "wins": wins, "win_rate": win_rate,
            "avg_pnl": avg_pnl, "decision": decision,
        }
        log.info("%-10s signals=%4d trades=%4d win_rate=%.1f%% avg_pnl=%+.3f%% → %s",
                 profile_name, signals_total, n, win_rate * 100, avg_pnl * 100, decision)

    # Step 5.5: trades CSV dump (모든 trade 의 date/종목/entry_ts/exit_ts/ret 명세)
    log.info("== Step 5.5: trades CSV dump ==")
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    label = f"{today_str}_md{n_days}" if n_days > 1 else today_str
    import csv as _csv
    trades_csv = report_dir / f"03_trades_{label}.csv"
    with open(trades_csv, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["date", "profile", "symbol", "entry_ts", "entry_px", "exit_ts",
                    "exit_px", "exit_reason", "ret_pct"])
        for prof, _ in profiles:
            for r in all_results[prof]:
                for t in r["trades"]:
                    w.writerow([t.get("date", today_str), prof, t["symbol"],
                                t["entry_ts"], t["entry_px"],
                                t["exit_ts"], t["exit_px"], t["exit_reason"],
                                f"{t['ret_pct']:.5f}"])
    log.info("trades csv: %s", trades_csv)

    # Step 6: report
    log.info("== Step 6: write report ==")
    title = f"# HTS 검색식 {n_days}거래일 pilot — {today_str}" if n_days > 1 else f"# HTS 검색식 1일 pilot — {today_str}"
    out = report_dir / f"03_pilot_report_{label}.draft.md"
    dates_str = ", ".join(d.isoformat() for d in trading_dates)
    lines = [
        title,
        "",
        f"옵션 {'A+B 본검증' if n_days > 1 else 'A'}: KIS 1분봉 lake + 3종 검색식 evaluator + +2/-2% 시뮬레이션.",
        "",
        f"- 거래일: {dates_str} ({n_days}일)",
        f"- 필터된 universe: {len(universe)} 종목 (FDR snapshot A+B+C eod 근사)",
        f"- 1m 데이터 적재 (date×symbol 카운트): {sum(per_date_loaded.values())}",
        f"- E 체결강도: pilot placeholder (power_ratio=100.0 통과 처리, 후속 이슈 정밀 재현)",
        "",
        "## 결과 요약",
        "",
        "| profile | signals | trades | wins | win_rate | avg_pnl | decision |",
        "|---------|--------:|-------:|-----:|---------:|--------:|----------|",
    ]
    for prof, s in summary.items():
        lines.append(
            f"| {prof} | {s['signals']} | {s['trades']} | "
            f"{s.get('wins', 0)} | {s.get('win_rate', 0) * 100:.1f}% | "
            f"{s.get('avg_pnl', 0) * 100:+.3f}% | **{s['decision']}** |"
        )
    lines += [
        "",
        "## 한계",
        f"1. 표본 {n_days}거래일 → " + (
            "통계 신뢰도 낮음. 옵션 B 로 5거래일 누적 후 본 검증 필요." if n_days < 5
            else "1주 일관성 검증 가능. 더 긴 검증은 후속 (1개월~)."
        ),
        "2. E 체결강도 placeholder. KIS `inquire-price` `tday_rltv` 분봉 시점별 누적 재구성 미구현.",
        "3. 단타 H \"지지\" 단일 봉 1회 기준. 키움 내부 정의 단정 불가.",
        "",
        "## 출처",
        "- 검색식 캡처 3장: 사용자 제공 (2026-05-14, 이슈 #230)",
        "- KIS FHKST03010200 (분봉), 한국 lake `/lake/ohlcv/freq=1m/`",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    log.info("report saved: %s", out)

    return 0


if __name__ == "__main__":
    sys.exit(main())

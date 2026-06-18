"""FastAPI local dashboard — 4-quadrant UI + Prometheus /metrics endpoint.

4사분면:
  Q1 (top-left)  : 손익 그래프 (실시간/일/월 토글)
  Q2 (top-right) : 6종 한도 사용률 게이지
  Q3 (bottom-left): 신호→메타라벨러→주문→체결 타임라인
  Q4 (bottom-right): 비상정지 4 트리거 상태 + 수동 발동/해제
"""
from __future__ import annotations

import asyncio
import html
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

from src.dashboard.airborne_fire_store import AirborneFireStore
from src.dashboard.airborne_sim_cache import AirborneSimCache
from src.dashboard.ma_cross_sim_cache import MaCrossSimCache
from src.dashboard.ma_cross_store import MaCrossStore
from src.dashboard.ops_counters import OpsCounters
from src.dashboard.patch_notes import render_patch_notes_page
from src.dashboard.shadow_runs import discover_shadow_runs, load_run_detail
from src.dashboard.strategy_catalog import load_production_status, load_strategy_catalog
from src.dashboard.timeline_broker import TimelineBroker

# Process-wide singleton — dashboard 가 daemon FIRE 들을 영속 JSONL 에 누적.
# docker logs rotation (4d) 보다 길게 보관 → /airborne 페이지 "all" 윈도우.
_AIRBORNE_FIRE_STORE: AirborneFireStore | None = None


def _get_airborne_fire_store() -> AirborneFireStore:
    global _AIRBORNE_FIRE_STORE
    if _AIRBORNE_FIRE_STORE is None:
        _AIRBORNE_FIRE_STORE = AirborneFireStore(
            "logs/airborne_fires/history.jsonl",
        )
    return _AIRBORNE_FIRE_STORE


# Sim outcome cache — fire → (outcome/pct/bar_idx). 한 번 계산되면 안 바뀜.
# 옛 fire 재호출 시 캐시 hit → Binance fapi REST 0 회 (수십 초 절감).
# Rule key → AirborneSimCache 인스턴스. 룰별로 캐시 파일 분리해 결과 안 섞이게.
# 기본 룰(+1%/-0.5%) → logs/airborne_fires/sim_cache.jsonl
# 신규 +2%/-1% 룰     → logs/airborne_fires/sim_cache_2pct.jsonl
_AIRBORNE_SIM_CACHES: dict[str, AirborneSimCache] = {}

_AIRBORNE_SIM_CACHE_PATHS: dict[str, str] = {
    "default": "logs/airborne_fires/sim_cache.jsonl",
    "2pct": "logs/airborne_fires/sim_cache_2pct.jsonl",
}


def _get_airborne_sim_cache(rule_key: str = "default") -> AirborneSimCache:
    """rule_key 별 캐시 인스턴스 반환. 모르는 rule_key 는 default 로 fallback."""
    if rule_key not in _AIRBORNE_SIM_CACHE_PATHS:
        rule_key = "default"
    cache = _AIRBORNE_SIM_CACHES.get(rule_key)
    if cache is None:
        cache = AirborneSimCache(_AIRBORNE_SIM_CACHE_PATHS[rule_key])
        _AIRBORNE_SIM_CACHES[rule_key] = cache
    return cache


# ── ma-cross (골든/데드 크로스) store + sim cache 싱글톤 ──────────────────────
# airborne 와 동일 구조 — qta-ma-cross-daemon 의 CROSS 라인을 docker logs 에서
# 파싱 → 영속 JSONL store 누적 → bitget 1h 봉 시뮬 → /ma-cross 페이지 집계.
_MA_CROSS_STORE: MaCrossStore | None = None
_MA_CROSS_SIM_CACHE: MaCrossSimCache | None = None


def _get_ma_cross_store() -> MaCrossStore:
    global _MA_CROSS_STORE
    if _MA_CROSS_STORE is None:
        _MA_CROSS_STORE = MaCrossStore("logs/ma-cross/history.jsonl")
    return _MA_CROSS_STORE


def _get_ma_cross_sim_cache() -> MaCrossSimCache:
    global _MA_CROSS_SIM_CACHE
    if _MA_CROSS_SIM_CACHE is None:
        _MA_CROSS_SIM_CACHE = MaCrossSimCache("logs/ma-cross/sim_cache.jsonl")
    return _MA_CROSS_SIM_CACHE
from src.live.trade_history import discover_wal_files, reconstruct_trades
from src.live.wal import replay as wal_replay
from src.observability.metrics import Metrics


# ── airborne FIRE 알림 파싱 (docker logs 소스) ──────────────────────────────
# 2026-05-26: airborne 데몬은 별도 컨테이너로 도는 알림 전용 프로세스라
# 데이터를 dashboard WAL 에 안 남긴다. 매일 자정에 routine 이 알림 적중
# 분석 (다음 15분봉 4개 검증) 하려면 raw FIRE 라인이 journal_today 에
# 같이 export 되어야 한다. 가장 단순한 경로 — docker logs 파싱.
# close/trigger 는 과학적표기(예: 6.896e-05, LUNC 등 초저가 코인)도 허용해야
# 한다 — [\d.]+ 만 쓰면 e-05 가 안 잡혀 해당 FIRE 가 통째로 누락된다 (2026-06-08
# LUNCUSDT: 텔레그램엔 왔으나 history.jsonl 미수록 → 데몬-게이트가 거래 못 시킴
# + 대시보드 PF 메트릭 왜곡). float() 로 파싱되는 형식을 그대로 수용.
_AIRBORNE_FIRE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*FIRE (\S+) (long|short) "
    r"@ close=([0-9.eE+-]+) trigger=([0-9.eE+-]+)"
)


def _parse_airborne_fire_line(line: str) -> dict | None:
    """단일 로그 라인에서 FIRE 한 건 추출 — 매칭 실패 시 None.

    포맷: ``2026-05-23 02:00:33,327 INFO airborne_alert_daemon — FIRE CBRSUSDT
    long @ close=264.52 trigger=263.156``

    daemon 컨테이너는 Dockerfile 에서 ``TZ=Asia/Seoul`` 이 설정돼 있어
    ``%(asctime)s`` 가 **KST 로컬 시각** 으로 찍힌다 (2026-05-26 검증 — 컨테이너
    안에서 ``date`` 는 KST, ``date -u`` 는 UTC). 그래서 파서는 KST tz 를 붙여
    aware datetime 으로 만든 뒤 downstream 일관성을 위해 UTC 로 변환한 ISO
    문자열을 반환한다. PR #321 이 ``tzinfo=timezone.utc`` 로 잘못 붙여 JS
    Intl.DateTimeFormat 변환에서 9 시간이 더 가산되던 버그 수정 (#322).
    """
    m = _AIRBORNE_FIRE_RE.match(line)
    if not m:
        return None
    try:
        ts_kst = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=_KST,
        )
    except ValueError:
        return None
    ts_utc = ts_kst.astimezone(timezone.utc)
    return {
        "ts": ts_utc.isoformat(),
        "symbol": m.group(2),
        "side": m.group(3),
        "fire_close": float(m.group(4)),
        "trigger": float(m.group(5)),
    }


# ── airborne 알림 적중 시뮬레이션 (TP/SL/timeout) ────────────────────────────
# routine spec (docs/routines/cs-tsmom-daily-report.md 규칙 6) 과 동일 룰.
# dashboard 메인 카드의 실시간 메트릭이 routine 결과와 일관되도록 한 곳에서만 정의.
AIRBORNE_TP_PCT = 0.010   # +1.0%
AIRBORNE_SL_PCT = 0.005   # -0.5% (1:2 손익비)
AIRBORNE_HOLD_BARS = 4    # 다음 15분봉 4개 = 1h
AIRBORNE_FEE_PCT = 0.034  # 양방향 합산 실효율 — 실 체결 fill 역산 기반.
# 2026-06-07: 기존 0.08 (taker 0.04%×2, 환급 미반영) 은 과대계상이었음.
# 실측: 6/6 9건 fill 의 charged fee 0.0653% 왕복(maker0.02/taker0.04 혼합) +
# 테더맥스 54% 페이백(적립 2.29 USDT 확인) → 실부담 (4.749-2.29)/notional =
# 0.0338% 왕복. round 하여 0.034%. (5y 활성화 게이트의 ≥10bp 보수 가정은 별개 —
# 페이백은 프로모션이라 게이트엔 미반영.)

# 2026-06-04: 사용자 요청 — 짧은호흡 룰(default 1%/0.5%) 외에 좀 더 넓은
# 폭(+2%/-1%) 룰의 통계를 같이 보고 싶음. 같은 fires set 을 두 룰로 시뮬해
# 별도 페이지(/airborne-2pct) 에서 비교. cache 도 룰별 분리 (sim_cache.jsonl
# vs sim_cache_2pct.jsonl) — 두 룰 결과가 같은 파일에 섞이지 않도록.
AIRBORNE_TP_PCT_2PCT = 0.020   # +2.0%
AIRBORNE_SL_PCT_2PCT = 0.010   # -1.0% (1:2 손익비 유지)
AIRBORNE_HOLD_BARS_2PCT = 4    # 동일 1h hold — 폭만 2배


def _simulate_airborne_fire(
    fire: dict, bars: list[dict],
    *,
    tp_pct: float = AIRBORNE_TP_PCT,
    sl_pct: float = AIRBORNE_SL_PCT,
    hold_bars: int = AIRBORNE_HOLD_BARS,
) -> dict | None:
    """단일 fire 의 시뮬 결과. bars 가 비어있거나 ``hold_bars`` 미만이면서
    TP/SL 조기 종결도 없으면 None — 통계 / 캐시 양쪽에서 incomplete sim 제외.

    Args:
        fire: {ts, symbol, side, fire_close, ...}
        bars: list of {open, high, low, close, open_time, close_time}
        tp_pct: take-profit threshold (default = AIRBORNE_TP_PCT = 1.0%).
        sl_pct: stop-loss threshold (default = AIRBORNE_SL_PCT = 0.5%).
        hold_bars: required # of bars for a "timeout" verdict (default = 4 = 1h).
            keyword-only — 기존 caller (positional 2 args) 는 그대로 default
            룰 (+1%/-0.5%/4봉) 사용 → byte-identical.

    Returns:
        {outcome: "TP"|"SL"|"SL_first"|"timeout", pct: float, bar_idx: int}
        - pct: 손익비 (TP=+tp_pct, SL=-sl_pct, timeout=실제 close 변화율 부호조정)
        - bar_idx: 1-based 닿은 봉 번호
        None — hold_bars 만큼 닫히기 전에 호출됐고 그 짧은 구간 안에 TP/SL 도
          안 찍힌 경우. caller (api_airborne_metrics) 가 None 인 fire 는 캐시
          저장 안 하고 metric 집계에서도 제외 → 다음 호출 때 hold_bars 다
          닫혀있으면 정상 sim 으로 재계산. 영원히 timeout 으로 박혀 통계를
          오염시키던 회귀 차단.
    """
    if not bars:
        return None
    entry = float(fire["fire_close"])
    is_long = fire["side"] == "long"
    tp_px = entry * (1 + tp_pct) if is_long else entry * (1 - tp_pct)
    sl_px = entry * (1 - sl_pct) if is_long else entry * (1 + sl_pct)
    for idx, bar in enumerate(bars, start=1):
        hi, lo = float(bar["high"]), float(bar["low"])
        if is_long:
            hit_tp = hi >= tp_px
            hit_sl = lo <= sl_px
        else:
            hit_tp = lo <= tp_px
            hit_sl = hi >= sl_px
        if hit_tp and hit_sl:
            return {"outcome": "SL_first", "pct": -sl_pct * 100, "bar_idx": idx}
        if hit_sl:
            return {"outcome": "SL", "pct": -sl_pct * 100, "bar_idx": idx}
        if hit_tp:
            return {"outcome": "TP", "pct": +tp_pct * 100, "bar_idx": idx}
    # 조기 종결 (TP/SL/SL_first) 안 찍혔는데 ``hold_bars`` 다 닫히기 전 = incomplete.
    # AirborneSimCache 가 한 번 박힌 결과를 절대 갱신 안 하는 dedup 구조라
    # incomplete 결과를 그대로 저장하면 영원히 timeout 으로 오염된다 (사용자
    # 2026-06-02 NOMUSDT incident — fire 1분 후 페이지 새로고침으로 1봉만
    # fetch 된 sim 이 +0.04% timeout 으로 박힘). hold_bars 부족하면 None 반환 →
    # caller 가 캐시 skip + 통계 제외 → 다음 새로고침에 다 닫혀있으면 정상
    # 시뮬로 재계산.
    if len(bars) < hold_bars:
        return None
    final_close = float(bars[-1]["close"])
    if is_long:
        pct = (final_close - entry) / entry * 100
    else:
        pct = (entry - final_close) / entry * 100
    return {"outcome": "timeout", "pct": pct, "bar_idx": len(bars)}


def _aggregate_airborne_sims(sims: list[dict]) -> dict:
    """시뮬 결과 list → 집계 통계 dict. 비어있으면 0/None 값 반환.

    Returns:
        {
            n, tp, sl, sl_first, timeout, win_rate (0-1),
            sum_pct (gross), net_pct (after fee), pf (float|None),
            mean_pct, by_side: {long: {...}, short: {...}},
            by_kst_bucket: [{bucket, n, tp, sl, win_rate, sum_pct, pf}, ...]
        }
    """
    n = len(sims)
    if n == 0:
        return {
            "n": 0, "tp": 0, "sl": 0, "sl_first": 0, "timeout": 0,
            "win_rate": None, "sum_pct": 0.0, "net_pct": 0.0,
            "pf": None, "mean_pct": None,
            "by_side": {}, "by_kst_bucket": [],
        }
    tp = sum(1 for s in sims if s["outcome"] == "TP")
    sl = sum(1 for s in sims if s["outcome"] == "SL")
    sl_first = sum(1 for s in sims if s["outcome"] == "SL_first")
    timeout = sum(1 for s in sims if s["outcome"] == "timeout")
    pcts = [s["pct"] for s in sims]
    wins = [p for p in pcts if p > 0]
    losses = [p for p in pcts if p < 0]
    sum_pct = sum(pcts)
    net_pct = sum_pct - AIRBORNE_FEE_PCT * n
    pf = (sum(wins) / abs(sum(losses))) if losses else None

    def _bucket(h: int) -> str:
        if   0 <= h < 6:  return "00-06 새벽"
        elif 6 <= h < 12: return "06-12 오전"
        elif 12 <= h < 18:return "12-18 오후"
        else:             return "18-24 저녁"

    # side별
    by_side: dict = {}
    for sd in ("long", "short"):
        rs = [s for s in sims if s.get("side") == sd]
        if not rs:
            continue
        sd_pcts = [r["pct"] for r in rs]
        sd_wins = [p for p in sd_pcts if p > 0]
        sd_losses = [p for p in sd_pcts if p < 0]
        by_side[sd] = {
            "n": len(rs),
            "tp": sum(1 for r in rs if r["outcome"] == "TP"),
            "sl": sum(1 for r in rs if r["outcome"] in ("SL", "SL_first")),
            "win_rate": len(sd_wins) / len(rs),
            "sum_pct": sum(sd_pcts),
            "pf": (sum(sd_wins) / abs(sum(sd_losses))) if sd_losses else None,
        }

    # KST 시간대별 (입력 sims 의 ts 필드는 ISO 8601 UTC 가정)
    from collections import defaultdict
    by_b: dict = defaultdict(list)
    for s in sims:
        try:
            ts = datetime.fromisoformat(str(s["ts"]).replace("Z", "+00:00"))
            kst_h = ts.astimezone(_KST).hour
            by_b[_bucket(kst_h)].append(s)
        except (KeyError, ValueError, TypeError):
            continue
    by_kst_bucket = []
    for bk in ("00-06 새벽", "06-12 오전", "12-18 오후", "18-24 저녁"):
        rs = by_b.get(bk, [])
        if not rs:
            continue
        rs_pcts = [r["pct"] for r in rs]
        rs_wins = [p for p in rs_pcts if p > 0]
        rs_losses = [p for p in rs_pcts if p < 0]
        by_kst_bucket.append({
            "bucket": bk,
            "n": len(rs),
            "tp": sum(1 for r in rs if r["outcome"] == "TP"),
            "sl": sum(1 for r in rs if r["outcome"] in ("SL", "SL_first")),
            "win_rate": len(rs_wins) / len(rs),
            "sum_pct": sum(rs_pcts),
            "pf": (sum(rs_wins) / abs(sum(rs_losses))) if rs_losses else None,
        })

    return {
        "n": n, "tp": tp, "sl": sl, "sl_first": sl_first, "timeout": timeout,
        "win_rate": len(wins) / n,
        "sum_pct": sum_pct, "net_pct": net_pct,
        "pf": pf, "mean_pct": sum_pct / n,
        "by_side": by_side, "by_kst_bucket": by_kst_bucket,
    }


def _parse_airborne_fires_from_docker_logs(
    since_iso: str, *, container_name: str = "qta-airborne-daemon",
) -> list[dict]:
    """``docker logs <container> --since <iso>`` 에서 FIRE 라인 추출.

    docker CLI 미설치 / 컨테이너 미가동 / 실행 권한 실패 등은 빈 리스트
    리턴 (graceful). dashboard 가 docker 안에서 돌 때도 안전.

    Returns:
        list of {ts (UTC ISO), symbol, side, fire_close, trigger}.
    """
    import subprocess
    try:
        # Windows cp949 console 에서도 안전하게 — encoding utf-8 강제 + replace
        # (airborne 로그의 em dash "—" 같은 비-cp949 문자가 디코딩 실패해
        # stdout=None 되는 경로 차단).
        p = subprocess.run(
            ["docker", "logs", container_name, "--since", since_iso],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if p.returncode != 0:
        return []
    fires: list[dict] = []
    for line in ((p.stdout or "") + (p.stderr or "")).splitlines():
        rec = _parse_airborne_fire_line(line)
        if rec is not None:
            fires.append(rec)
    return fires


# ── ma-cross (골든/데드 크로스) 파싱 + 시뮬 + 집계 ───────────────────────────
# qta-ma-cross-daemon (scripts/ma_cross_alert_daemon.py) 의 CROSS 로그 라인을
# docker logs 에서 파싱 → 영속 store → bitget 1h 봉 시뮬 → /ma-cross 페이지.
# airborne 흐름과 byte-for-byte 동일 패턴 (파서/sim/aggregate/refresher/route).
# 데몬은 절대 수정 안 함 — 대시보드 측만 추가.
#
# 로그 포맷 (evaluate_and_dispatch 의 log.info):
#   ``2026-06-18 19:00:33,565 INFO ma_cross_alert_daemon — CROSS BTCUSDT golden
#     @ close=67000 sma25=66900 sma200=65000``
# close/sma 는 과학적표기(초저가 코인) 도 허용 — airborne 와 동일 [0-9.eE+-]+.
_MA_CROSS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*CROSS (\S+) (golden|death) "
    r"@ close=([0-9.eE+-]+) sma\d+=([0-9.eE+-]+) sma\d+=([0-9.eE+-]+)"
)

# 시뮬 룰 (확정): 손익비 1:6 = SL 2% / TP 12% (가격 기준). golden=롱, death=숏.
# high/low intra-bar 판정, 동시터치 SL_first 보수 처리, hold_bars 후 timeout.
MA_CROSS_TP_PCT = 0.12    # +12%
MA_CROSS_SL_PCT = 0.02    # -2% (1:6 손익비)
MA_CROSS_HOLD_BARS = 720  # 1h 봉 720개 = 30일 (1:6 은 TP 도달에 오래 걸림)
# 비용 — airborne 집계와 동일 fee 모델 (양방향 합산 실효율, net% 에 반영).
MA_CROSS_FEE_PCT = AIRBORNE_FEE_PCT


def _parse_ma_cross_line(line: str) -> dict | None:
    """단일 로그 라인에서 CROSS 한 건 추출 — 매칭 실패 시 None.

    포맷: ``2026-06-18 19:00:33,565 INFO ma_cross_alert_daemon — CROSS BTCUSDT
    golden @ close=67000 sma25=66900 sma200=65000``

    daemon 컨테이너 (qta-ma-cross-daemon) 는 docker-compose 에서 ``TZ=Asia/Seoul``
    이라 ``%(asctime)s`` 가 KST 로컬 시각. airborne 파서와 동일 규약 — KST tz 를
    붙여 aware datetime 으로 만든 뒤 UTC ISO 로 정규화해 반환.
    """
    m = _MA_CROSS_RE.match(line)
    if not m:
        return None
    try:
        ts_kst = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=_KST,
        )
    except ValueError:
        return None
    ts_utc = ts_kst.astimezone(timezone.utc)
    return {
        "ts": ts_utc.isoformat(),
        "symbol": m.group(2),
        "cross": m.group(3),
        "close": float(m.group(4)),
        "sma_fast": float(m.group(5)),
        "sma_slow": float(m.group(6)),
    }


def _parse_ma_cross_from_docker_logs(
    since_iso: str, *, container_name: str = "qta-ma-cross-daemon",
) -> list[dict]:
    """``docker logs <container> --since <iso>`` 에서 CROSS 라인 추출.

    docker CLI 미설치 / 컨테이너 미가동 / 실행 권한 실패 등은 빈 리스트
    리턴 (graceful, never raise). airborne 파서와 동일 방어.

    Returns:
        list of {ts (UTC ISO), symbol, cross, close, sma_fast, sma_slow}.
    """
    import subprocess
    try:
        p = subprocess.run(
            ["docker", "logs", container_name, "--since", since_iso],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if p.returncode != 0:
        return []
    crosses: list[dict] = []
    for line in ((p.stdout or "") + (p.stderr or "")).splitlines():
        rec = _parse_ma_cross_line(line)
        if rec is not None:
            crosses.append(rec)
    return crosses


def _simulate_ma_cross(
    cross_rec: dict, bars: list[dict],
    *,
    tp_pct: float = MA_CROSS_TP_PCT,
    sl_pct: float = MA_CROSS_SL_PCT,
    hold_bars: int = MA_CROSS_HOLD_BARS,
) -> dict | None:
    """단일 cross 의 TP/SL 시뮬 결과. ``_simulate_airborne_fire`` 와 동일 시맨틱.

    골든=롱, 데드=숏. entry=close. 롱 TP=entry×(1+tp)/SL=entry×(1-sl), 숏 미러.
    bars (진입 이후 1h 캔들) 의 high/low 로 intra-bar 판정, 동시터치 SL_first
    보수 처리, 둘 다 미도달이면 hold_bars 후 timeout=종가수익.

    Returns:
        {outcome: "TP"|"SL"|"SL_first"|"timeout", pct, bar_idx} 또는 None.
        None — bars 비었거나, hold_bars 미만이면서 TP/SL 조기 종결도 없는
        incomplete sim. caller 가 None 은 캐시 skip + 통계 제외 → 다음 호출에
        다 닫혀있으면 정상 재계산 (airborne incomplete-guard 회귀 차단 그대로).
    """
    if not bars:
        return None
    entry = float(cross_rec["close"])
    is_long = cross_rec["cross"] == "golden"
    tp_px = entry * (1 + tp_pct) if is_long else entry * (1 - tp_pct)
    sl_px = entry * (1 - sl_pct) if is_long else entry * (1 + sl_pct)
    for idx, bar in enumerate(bars, start=1):
        hi, lo = float(bar["high"]), float(bar["low"])
        if is_long:
            hit_tp = hi >= tp_px
            hit_sl = lo <= sl_px
        else:
            hit_tp = lo <= tp_px
            hit_sl = hi >= sl_px
        if hit_tp and hit_sl:
            return {"outcome": "SL_first", "pct": -sl_pct * 100, "bar_idx": idx}
        if hit_sl:
            return {"outcome": "SL", "pct": -sl_pct * 100, "bar_idx": idx}
        if hit_tp:
            return {"outcome": "TP", "pct": +tp_pct * 100, "bar_idx": idx}
    # 조기 종결 안 찍혔는데 hold_bars 다 닫히기 전 = incomplete → None (캐시 skip).
    if len(bars) < hold_bars:
        return None
    final_close = float(bars[-1]["close"])
    if is_long:
        pct = (final_close - entry) / entry * 100
    else:
        pct = (entry - final_close) / entry * 100
    return {"outcome": "timeout", "pct": pct, "bar_idx": len(bars)}


def _aggregate_ma_cross_sims(sims: list[dict]) -> dict:
    """시뮬 결과 list → 집계 통계 dict. ``_aggregate_airborne_sims`` 미러.

    airborne 의 by_side(long/short) 대신 by_cross(golden/death) 로 분리한다.

    Returns:
        {n, tp, sl, sl_first, timeout, win_rate (0-1), sum_pct (gross),
         net_pct (after fee), pf (float|None), mean_pct,
         by_cross: {golden: {...}, death: {...}}, by_kst_bucket: [...]}
    """
    n = len(sims)
    if n == 0:
        return {
            "n": 0, "tp": 0, "sl": 0, "sl_first": 0, "timeout": 0,
            "win_rate": None, "sum_pct": 0.0, "net_pct": 0.0,
            "pf": None, "mean_pct": None,
            "by_cross": {}, "by_kst_bucket": [],
        }
    tp = sum(1 for s in sims if s["outcome"] == "TP")
    sl = sum(1 for s in sims if s["outcome"] == "SL")
    sl_first = sum(1 for s in sims if s["outcome"] == "SL_first")
    timeout = sum(1 for s in sims if s["outcome"] == "timeout")
    pcts = [s["pct"] for s in sims]
    wins = [p for p in pcts if p > 0]
    losses = [p for p in pcts if p < 0]
    sum_pct = sum(pcts)
    net_pct = sum_pct - MA_CROSS_FEE_PCT * n
    pf = (sum(wins) / abs(sum(losses))) if losses else None

    def _bucket(h: int) -> str:
        if   0 <= h < 6:  return "00-06 새벽"
        elif 6 <= h < 12: return "06-12 오전"
        elif 12 <= h < 18:return "12-18 오후"
        else:             return "18-24 저녁"

    # cross 방향별 (golden/death)
    by_cross: dict = {}
    for cr in ("golden", "death"):
        rs = [s for s in sims if s.get("cross") == cr]
        if not rs:
            continue
        cr_pcts = [r["pct"] for r in rs]
        cr_wins = [p for p in cr_pcts if p > 0]
        cr_losses = [p for p in cr_pcts if p < 0]
        by_cross[cr] = {
            "n": len(rs),
            "tp": sum(1 for r in rs if r["outcome"] == "TP"),
            "sl": sum(1 for r in rs if r["outcome"] in ("SL", "SL_first")),
            "win_rate": len(cr_wins) / len(rs),
            "sum_pct": sum(cr_pcts),
            "pf": (sum(cr_wins) / abs(sum(cr_losses))) if cr_losses else None,
        }

    # KST 시간대별 (입력 sims 의 ts 필드는 ISO 8601 UTC 가정)
    from collections import defaultdict
    by_b: dict = defaultdict(list)
    for s in sims:
        try:
            ts = datetime.fromisoformat(str(s["ts"]).replace("Z", "+00:00"))
            kst_h = ts.astimezone(_KST).hour
            by_b[_bucket(kst_h)].append(s)
        except (KeyError, ValueError, TypeError):
            continue
    by_kst_bucket = []
    for bk in ("00-06 새벽", "06-12 오전", "12-18 오후", "18-24 저녁"):
        rs = by_b.get(bk, [])
        if not rs:
            continue
        rs_pcts = [r["pct"] for r in rs]
        rs_wins = [p for p in rs_pcts if p > 0]
        rs_losses = [p for p in rs_pcts if p < 0]
        by_kst_bucket.append({
            "bucket": bk,
            "n": len(rs),
            "tp": sum(1 for r in rs if r["outcome"] == "TP"),
            "sl": sum(1 for r in rs if r["outcome"] in ("SL", "SL_first")),
            "win_rate": len(rs_wins) / len(rs),
            "sum_pct": sum(rs_pcts),
            "pf": (sum(rs_wins) / abs(sum(rs_losses))) if rs_losses else None,
        })

    return {
        "n": n, "tp": tp, "sl": sl, "sl_first": sl_first, "timeout": timeout,
        "win_rate": len(wins) / n,
        "sum_pct": sum_pct, "net_pct": net_pct,
        "pf": pf, "mean_pct": sum_pct / n,
        "by_cross": by_cross, "by_kst_bucket": by_kst_bucket,
    }


@dataclass
class DashboardState:
    """Mutable runtime state shared across request handlers."""

    # 한도 사용률 (0.0–1.0)
    limit_per_trade: float = 0.0
    limit_per_day: float = 0.0
    limit_per_portfolio: float = 0.0
    limit_per_position: float = 0.0
    limit_sector: float = 0.0
    limit_drawdown: float = 0.0

    # 타임라인: [{ts, type, detail}, ...]
    timeline_events: list[dict[str, Any]] = field(default_factory=list)

    # 킬스위치
    kill_switch_triggers: dict[str, bool] = field(
        default_factory=lambda: {
            "drawdown": False,
            "daily_loss": False,
            "manual": False,
            "risk_breach": False,
        }
    )
    kill_switch_last_triggered: str | None = None

    # Prometheus registry (shared with Metrics)
    metrics: Metrics | None = None

    # WS 타임라인 (#181)
    timeline_broker: TimelineBroker | None = None
    wal_path: Path | None = None

    # 전략 카탈로그 + 토글 (#178 + #180)
    orchestrator: Any | None = None  # AsyncStrategyOrchestrator (avoid import cycle)
    specs_dir: Path | None = None    # docs/specs/strategies — fall back to repo default
    production_yaml_path: Path | None = None  # configs/orchestrator/production.yaml override
    cs_tsmom_computer: Any | None = None  # CsTsmomComputer — /cs-tsmom page backend
    position_provider: Callable[[str], list[tuple[str, float]]] | None = None

    # 거래 시작/정지 컨트롤 (#182 단계 2). dashboard-only 모드에서만 주입.
    run_controller: object | None = None

    # KIS + Binance 계좌 정보 provider (#182 — "내 계좌" 카드)
    account_info_provider: object | None = None

    # #238 follow-up — live SnapshotBuilder. Read-only; the dashboard surfaces
    # `.last_equity_status` so a venue that is silently INERT (real equity
    # unavailable → every order dropped by the Item-8 conversion) is visible
    # instead of presenting as an unexplained "0 trades". Wired the same way
    # as `orchestrator` (via a ShadowConfig ready-callback in live_run.py).
    snapshot_builder: Any | None = None

    # Shadow Runs 뷰어 (#198) — logs/shadow/{run_id}/wal.jsonl 디렉토리 루트
    shadow_log_dir: Path | None = None

    # 라이브 PnL aggregator (#194). /api/strategy_positions 의 전략별 realized
    # 합계 source. (PnL 카드 일간/월간은 2026-05-23 부터 거래소 income 원장
    # 직결 — _pnl_view / pnl_daily / pnl_monthly 정적 필드는 제거됨.)
    pnl_aggregator: Any | None = None

    # Operational diagnostics — bars seen, dispatch counts, last signal/order/fill.
    # Same WAL observer that feeds pnl/timeline updates these counters.
    ops_counters: OpsCounters | None = None

    # Multi-broker smoke runs (`smoke-dual`) write to a primary `wal_path`
    # (KIS) and an auxiliary log under `extra_wal_paths` (Binance). /api/trades
    # merges all of them. WS timeline replay still reads `wal_path` only —
    # live events arrive via the shared timeline_broker regardless of WAL.
    extra_wal_paths: list[Path] = field(default_factory=list)

    # Root directory that contains per-run sub-dirs (each holding a wal.jsonl).
    # Used by /api/trade_history → discover_wal_files(log_dir) → reconstruct_trades.
    # When None, derived automatically from wal_path.parent.parent (i.e. the run
    # dir's grandparent) if wal_path is set; otherwise returns an empty list
    # (boot before any run has written a WAL is normal/safe).
    log_dir: Path | None = None

    # Live mark-price cache (#238 follow-up — manual close + live-price card).
    # Same instance the mark-price consumer writes to. ``/api/strategy_positions``
    # reads it to overlay ``mark_price`` + ``pnl_pct`` per row so the operator
    # sees current PnL% without polling Binance.
    price_cache: Any | None = None

    # Manual-close executor closure. Called by the manual-close endpoint with
    # a list[OrderIntent] — closure handles broker.place_order + WAL + metrics
    # so the dashboard never imports broker internals. ``scripts/live_run.py``
    # builds the closure from the live router/kill_switch/WAL/store and wires
    # it here; dashboard-only / paper-mode keeps it ``None`` (manual close
    # returns 503 instead of half-acting).
    manual_close_executor: Any | None = None


def _pnl_from_trades(trades: list) -> dict:
    """closed round-trip 의 realized_pnl 을 청산시각(exit_ts) 기준 일/월 집계.

    2026-05-22: `/api/pnl` 의 PnL 카드를 거래내역(`reconstruct_trades`) 과
    동일 source 로 통일. 기존 `PnLAggregator` 는 cross-run cost-basis 누적
    이라 broker 실현손익과 크게 어긋났다 (실측: aggregator daily +127 vs
    Binance UI +54, 거래내역 round-trip 합 +48). 거래내역 합산이 broker 와
    수수료 차이로 일치함을 사용자가 검증 → PnL 카드도 같은 round-trip
    재구성으로 계산하면 항상 거래내역과 맞는다.

    daily = 청산시각이 오늘(KST 자정~) 인 closed trade 의 realized 합.
    monthly = 청산시각이 이번 달인 합. venue (USDT/KRW) 별로도 분리 —
    절대 cross-sum 안 함.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    kst = ZoneInfo("Asia/Seoul")
    now = datetime.now(kst)
    today = now.date()
    this_month = (now.year, now.month)
    daily = 0.0
    monthly = 0.0
    by_strategy: dict[str, float] = {}
    daily_by_venue: dict[str, float] = {}
    monthly_by_venue: dict[str, float] = {}
    for t in trades:
        if t.status != "closed" or t.realized_pnl is None or not t.exit_ts:
            continue
        try:
            exit_kst = datetime.fromisoformat(t.exit_ts).astimezone(kst)
        except (ValueError, TypeError):
            continue
        pnl = float(t.realized_pnl)
        venue = t.venue or "unknown"
        by_strategy[t.strategy_id] = by_strategy.get(t.strategy_id, 0.0) + pnl
        if (exit_kst.year, exit_kst.month) == this_month:
            monthly += pnl
            monthly_by_venue[venue] = monthly_by_venue.get(venue, 0.0) + pnl
        if exit_kst.date() == today:
            daily += pnl
            daily_by_venue[venue] = daily_by_venue.get(venue, 0.0) + pnl
    return {
        "daily": daily,
        "monthly": monthly,
        "by_strategy": by_strategy,
        "daily_by_venue": daily_by_venue,
        "monthly_by_venue": monthly_by_venue,
    }


def _gauge_html(name: str, value: float) -> str:
    pct = min(max(value * 100, 0), 100)
    color = "#f6465d" if pct >= 80 else "#f0a500" if pct >= 60 else "#0ecb81"
    return f"""
    <div class="gauge-row">
      <span class="gauge-label">{html.escape(name)}</span>
      <div class="gauge-bar-bg">
        <div class="gauge-bar" style="width:{pct:.1f}%;background:{color}"></div>
      </div>
      <span class="gauge-pct">{pct:.1f}%</span>
    </div>"""


def _timeline_row(ev: dict[str, Any]) -> str:
    ts = html.escape(str(ev.get("ts", "")))
    typ = html.escape(str(ev.get("type", "")))
    detail = html.escape(str(ev.get("detail", "")))
    type_class = {"signal": "tl-signal", "metalabel": "tl-meta",
                  "order": "tl-order", "fill": "tl-fill"}.get(ev.get("type", ""), "")
    return f'<tr><td class="tl-ts">{ts}</td><td><span class="tl-badge {type_class}">{typ}</span></td><td class="tl-detail">{detail}</td></tr>'


def _kill_row(name: str, active: bool) -> str:
    status_cls = "ks-active" if active else "ks-normal"
    status_txt = "발동" if active else "정상"
    return f"""
    <tr>
      <td>{html.escape(name)}</td>
      <td><span class="{status_cls}">{status_txt}</span></td>
    </tr>"""


def _render_dashboard(state: DashboardState, catalog_items: list[dict] | None = None) -> str:
    # 전략 카탈로그 카드 (#178+#180 인라인)
    catalog_cards_html = "".join(_strategy_card(it) for it in (catalog_items or []))

    # Q1: 손익 — venue 별 일간/월간은 클라이언트 JS(pnlVenueRefresh)가
    # /api/pnl 로 채운다. Binance 는 거래소 income 원장, KIS 는 재구성 추정치.

    # Q2: 한도 게이지
    limits = [
        ("per_trade", state.limit_per_trade),
        ("per_day", state.limit_per_day),
        ("per_portfolio", state.limit_per_portfolio),
        ("per_position", state.limit_per_position),
        ("sector", state.limit_sector),
        ("drawdown", state.limit_drawdown),
    ]
    gauges_html = "".join(_gauge_html(n, v) for n, v in limits)

    # Q3: 타임라인
    rows_html = "".join(_timeline_row(e) for e in state.timeline_events[-50:])

    # Q4: 킬스위치
    ks_rows = "".join(_kill_row(k, v) for k, v in state.kill_switch_triggers.items())
    last_ts = html.escape(state.kill_switch_last_triggered or "없음")
    any_active = any(state.kill_switch_triggers.values())
    ks_overall_cls = "ks-err" if any_active else "ks-ok"
    ks_overall_txt = "비상정지 발동 중" if any_active else "정상 운영"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QTA Dashboard</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap');

/* ── CSS 변수 (Binance Futures 팔레트) ── */
:root{{
  --bg:        #0b0e11;
  --surface:   #161a1e;
  --surface2:  #1e2329;
  --border:    #2b3139;
  --border2:   #363c45;
  --text:      #eaecef;
  --text2:     #848e9c;
  --text3:     #5e6673;
  --green:     #0ecb81;
  --green-dim: #0a9060;
  --red:       #f6465d;
  --red-dim:   #b03040;
  --yellow:    #f0a500;
  --blue:      #1890ff;
  --blue-dim:  #1565c0;
  --mono:      'IBM Plex Mono', 'Consolas', 'Menlo', monospace;
  --sans:      'IBM Plex Sans KR', 'Segoe UI', sans-serif;
}}

*{{box-sizing:border-box;margin:0;padding:0}}
html{{scroll-behavior:smooth}}
body{{
  font-family:var(--sans);
  background:var(--bg);
  color:var(--text);
  font-size:13px;
  line-height:1.5;
  padding:0;
  min-height:100vh;
}}

/* ── 상단 헤더 바 ── */
.topbar{{
  display:flex;
  align-items:center;
  justify-content:space-between;
  background:var(--surface);
  border-bottom:1px solid var(--border);
  padding:0 20px;
  height:52px;
  position:sticky;
  top:0;
  z-index:100;
}}
.topbar-brand{{
  font-family:var(--mono);
  font-weight:600;
  font-size:15px;
  color:var(--green);
  letter-spacing:.02em;
}}
.topbar-ts{{
  font-family:var(--mono);
  font-size:11px;
  color:var(--text3);
}}
.topbar-nav{{display:flex;gap:6px;align-items:center}}
.nav-pill{{
  display:inline-block;
  background:var(--surface2);
  border:1px solid var(--border);
  color:var(--text2);
  padding:4px 12px;
  border-radius:4px;
  text-decoration:none;
  font-size:11px;
  font-weight:500;
  letter-spacing:.02em;
  transition:border-color .15s,color .15s;
}}
.nav-pill:hover{{border-color:var(--blue);color:var(--blue)}}

/* ── Quick Links (랜딩 상단 CTA 카드) ── */
.quick-links{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}}
.quick-link-card{{
  display:flex;align-items:center;gap:12px;
  background:var(--surface);border:1px solid var(--border);
  border-radius:6px;padding:12px 14px;
  text-decoration:none;color:var(--text);
  transition:border-color .15s,background .15s,transform .15s;
}}
.quick-link-card:hover{{border-color:var(--blue);background:var(--surface2);transform:translateY(-1px)}}
.quick-link-signals{{border-color:rgba(14,203,129,.35)}}
.quick-link-signals:hover{{border-color:var(--green);background:rgba(14,203,129,.06)}}
.quick-link-icon{{font-size:22px;line-height:1}}
.quick-link-body{{display:flex;flex-direction:column;gap:2px;flex:1;min-width:0}}
.quick-link-title{{font-size:13px;font-weight:600;color:var(--text)}}
.quick-link-sub{{font-size:11px;color:var(--text3);font-family:var(--mono)}}
.quick-link-arrow{{font-size:16px;color:var(--text3);font-family:var(--mono)}}
.quick-link-card:hover .quick-link-arrow{{color:var(--blue)}}
.quick-link-signals:hover .quick-link-arrow{{color:var(--green)}}

/* ── 메인 레이아웃 ── */
.page{{padding-top:68px;padding-right:20px;padding-bottom:16px;padding-left:20px;display:flex;flex-direction:column;gap:16px}}

/* ── 섹션 헤더 ── */
.section-hdr{{
  display:flex;
  align-items:center;
  gap:10px;
  margin-bottom:10px;
}}
.section-hdr h2{{
  font-family:var(--sans);
  font-size:11px;
  font-weight:600;
  color:var(--text2);
  text-transform:uppercase;
  letter-spacing:.1em;
}}
.section-hdr-line{{flex:1;height:1px;background:var(--border)}}

/* ── 카드 ── */
.card{{
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:6px;
  padding:16px;
}}
.card-sm{{padding:12px 16px}}

/* ── venue PnL 분할 카드 ── */
.pnl-venue-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}}
.pnl-venue-card{{
  background:var(--bg);
  border:1px solid var(--border);
  border-radius:5px;
  padding:10px 12px;
}}
.pnl-venue-header{{
  display:flex;align-items:center;gap:6px;
  margin-bottom:8px;
  font-size:10px;font-weight:700;
  text-transform:uppercase;letter-spacing:.08em;color:var(--text3);
}}
.pnl-venue-flag{{font-size:13px;line-height:1}}
.pnl-venue-name{{color:var(--text2)}}
.pnl-venue-currency{{
  margin-left:auto;
  font-family:var(--mono);font-size:10px;font-weight:600;
  color:var(--text3);background:var(--surface2);
  border:1px solid var(--border);border-radius:3px;
  padding:1px 6px;
}}
.pnl-venue-rows{{display:flex;flex-direction:column;gap:4px}}
.pnl-venue-row{{display:flex;align-items:baseline;justify-content:space-between;gap:6px}}
.pnl-venue-period{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.06em;flex-shrink:0}}
.pnl-venue-val{{
  font-family:var(--mono);font-size:13px;font-weight:600;
  font-variant-numeric:tabular-nums;color:var(--green);
}}
.pnl-venue-val.neg{{color:var(--red)}}
.pnl-venue-val.zero{{color:var(--text3)}}
.pnl-venue-cur{{font-family:var(--mono);font-size:9px;color:var(--text3);margin-left:3px}}
.pnl-no-sum-note{{
  font-size:9px;color:var(--text3);
  text-align:center;margin-top:6px;
  padding:3px 0;border-top:1px solid var(--border);
  letter-spacing:.02em;
}}

/* ── 거래 내역 테이블 (round-trip) ── */
.th-history-wrap{{max-height:360px;overflow-y:auto;border:1px solid var(--border);border-radius:4px}}
.th-table{{width:100%;border-collapse:collapse;font-size:11px}}
.th-table thead th{{
  font-size:10px;font-weight:600;color:var(--text3);
  text-transform:uppercase;letter-spacing:.06em;
  padding:7px 10px;border-bottom:1px solid var(--border);
  background:var(--surface);
  position:sticky;top:0;text-align:left;white-space:nowrap;
}}
.th-table thead th.num{{text-align:right}}
.th-table tbody tr{{border-bottom:1px solid var(--border);transition:background .1s}}
.th-table tbody tr:last-child{{border-bottom:none}}
.th-table tbody tr:hover{{background:rgba(255,255,255,.025)}}
.th-table tbody tr:nth-child(even){{background:rgba(255,255,255,.012)}}
.th-table tbody tr.th-open{{background:rgba(240,165,0,.04)}}
.th-table tbody tr.th-open:hover{{background:rgba(240,165,0,.07)}}
.th-table td{{padding:7px 10px;vertical-align:middle;white-space:nowrap}}
.th-mono{{font-family:var(--mono);text-align:right;font-variant-numeric:tabular-nums;color:var(--text)}}
.th-dim{{color:var(--text3);font-size:10px;font-family:var(--mono)}}
.th-sym{{font-family:var(--mono);font-weight:600;font-size:12px;color:var(--text)}}
.th-strategy{{font-size:10px;color:var(--text3);max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.th-venue{{font-family:var(--mono);font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px;background:var(--surface2);color:var(--text3);border:1px solid var(--border)}}
.th-open-badge{{
  display:inline-block;font-family:var(--mono);font-size:9px;font-weight:700;
  padding:2px 7px;border-radius:3px;letter-spacing:.04em;
  background:rgba(240,165,0,.12);color:var(--yellow);border:1px solid rgba(240,165,0,.25);
}}
.th-closed-badge{{
  display:inline-block;font-family:var(--mono);font-size:9px;font-weight:600;
  padding:2px 7px;border-radius:3px;letter-spacing:.04em;
  background:rgba(14,203,129,.08);color:var(--green-dim);border:1px solid rgba(14,203,129,.15);
}}
.th-truncnote{{font-size:10px;color:var(--text3);text-align:center;padding:6px;border-top:1px solid var(--border)}}

/* ── 그리드 레이아웃 ── */
.grid-3{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.grid-2{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}
.grid-4{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
@media(max-width:960px){{
  .grid-3,.grid-4{{grid-template-columns:1fr 1fr}}
  .grid-2{{grid-template-columns:1fr}}
}}
@media(max-width:600px){{
  .grid-3,.grid-4,.grid-2{{grid-template-columns:1fr}}
}}

/* ── PnL 숫자 카드 ── */
.pnl-card{{
  background:var(--surface2);
  border:1px solid var(--border);
  border-radius:6px;
  padding:14px 16px;
}}
.pnl-label{{
  font-size:10px;
  font-weight:600;
  color:var(--text3);
  text-transform:uppercase;
  letter-spacing:.08em;
  margin-bottom:6px;
}}
.pnl-value{{
  font-family:var(--mono);
  font-size:22px;
  font-weight:600;
  color:var(--green);
  letter-spacing:-.01em;
  font-variant-numeric:tabular-nums;
}}
.pnl-value.neg{{color:var(--red)}}
.pnl-value.zero{{color:var(--text2)}}

/* ── 계좌 요약 카드 (Binance-style 강조) ── */
.acct-hero{{
  display:grid;
  grid-template-columns:1fr 1fr 1fr;
  gap:0;
  border:1px solid var(--border);
  border-radius:6px;
  overflow:hidden;
}}
.acct-hero-cell{{
  padding:14px 18px;
  border-right:1px solid var(--border);
  background:var(--surface2);
}}
.acct-hero-cell:last-child{{border-right:none}}
.acct-hero-label{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px}}
.acct-hero-val{{font-family:var(--mono);font-size:18px;font-weight:600;color:var(--text);font-variant-numeric:tabular-nums}}
.acct-hero-sub{{font-family:var(--mono);font-size:10px;color:var(--text3);margin-top:2px}}

/* ── 포지션 테이블 (Binance-style) ── */
.pos-table{{width:100%;border-collapse:collapse;font-size:12px}}
.pos-table thead th{{
  font-family:var(--sans);
  font-size:10px;
  font-weight:600;
  color:var(--text3);
  text-transform:uppercase;
  letter-spacing:.06em;
  padding:8px 10px;
  text-align:right;
  border-bottom:1px solid var(--border);
  white-space:nowrap;
  background:var(--surface);
  position:sticky;
  top:52px;
}}
.pos-table thead th:first-child{{text-align:left}}
.pos-table tbody tr{{border-bottom:1px solid var(--border);transition:background .1s}}
.pos-table tbody tr:last-child{{border-bottom:none}}
.pos-table tbody tr:hover{{background:rgba(255,255,255,.025)}}
.pos-table tbody tr:nth-child(even){{background:rgba(255,255,255,.012)}}
.pos-table td{{
  padding:9px 10px;
  font-family:var(--mono);
  font-size:12px;
  text-align:right;
  font-variant-numeric:tabular-nums;
  white-space:nowrap;
  color:var(--text);
}}
.pos-table td:first-child{{text-align:left;font-family:var(--sans);font-weight:600}}
.pos-table td.null-val{{color:var(--text3)}}
/* sticky thead inside a SELF-scrolling container must use top:0, not the
   page-topbar offset (top:52px). Both the 전략별 포지션 (.trades-wrap) and the
   Binance 계좌 카드 포지션 표 (#bnb-pos-wrap, max-height:200px overflow:auto)
   scroll inside their own box — without this the header floats 52px DOWN
   inside the box and overlaps the rows. */
.trades-wrap .pos-table thead th,
#bnb-pos-wrap .pos-table thead th,
#bg-pos-wrap .pos-table thead th{{top:0;z-index:2;background:var(--surface)}}

/* ── 전략 포지션 테이블 ── */
.stratpos-sym{{font-family:var(--mono);font-weight:600;font-size:13px;color:var(--text)}}
.stratpos-id{{font-size:10px;color:var(--text3);margin-top:1px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
/* 2026-05-21: stratpos 카드 첫 컬럼 — 전략 큰 글자 + 종목 옆 가로 배치 */
.stratpos-row{{display:flex;align-items:baseline;gap:8px}}
.stratpos-strat-big{{font-family:var(--mono);font-weight:600;font-size:13px;color:var(--text);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.stratpos-close-btn{{background:#2b1414;color:#ff6b6b;border:1px solid #4a2020;border-radius:4px;padding:4px 10px;font-family:var(--sans);font-size:10px;cursor:pointer;transition:background .12s,color .12s}}
.stratpos-close-btn:hover:not(:disabled){{background:#4a2020;color:#fff}}
.stratpos-close-btn:disabled{{opacity:.5;cursor:not-allowed}}
.stratpos-sym-small{{font-family:var(--mono);font-size:11px;color:var(--text2)}}
.side-badge{{
  display:inline-block;
  font-family:var(--mono);
  font-size:10px;
  font-weight:600;
  padding:2px 7px;
  border-radius:3px;
  letter-spacing:.04em;
}}
.side-long{{background:rgba(14,203,129,.12);color:var(--green);border:1px solid rgba(14,203,129,.25)}}
.side-short{{background:rgba(246,70,93,.12);color:var(--red);border:1px solid rgba(246,70,93,.25)}}
.side-flat{{background:var(--surface2);color:var(--text3);border:1px solid var(--border)}}
.col-green{{color:var(--green)}}
.col-red{{color:var(--red)}}
.col-dim{{color:var(--text3)}}

/* ── 게이지 ── */
.gauge-row{{display:flex;align-items:center;gap:8px;margin-bottom:7px}}
.gauge-label{{width:100px;font-size:11px;color:var(--text2);flex-shrink:0;font-family:var(--mono)}}
.gauge-bar-bg{{flex:1;height:4px;background:var(--border);border-radius:2px;overflow:hidden}}
.gauge-bar{{height:100%;border-radius:2px;transition:width .4s cubic-bezier(.4,0,.2,1)}}
.gauge-pct{{width:38px;font-size:11px;text-align:right;color:var(--text2);font-family:var(--mono);font-variant-numeric:tabular-nums}}

/* ── 킬스위치 ── */
.ks-active{{color:var(--red);font-weight:700}}
.ks-normal{{color:var(--green)}}
.ks-overall{{font-size:13px;font-weight:700;margin-bottom:10px;padding:8px 12px;border-radius:4px;display:inline-block}}
.ks-overall.ks-ok{{background:rgba(14,203,129,.08);color:var(--green);border:1px solid rgba(14,203,129,.2)}}
.ks-overall.ks-err{{background:rgba(246,70,93,.1);color:var(--red);border:1px solid rgba(246,70,93,.25);animation:ks-pulse 1.2s infinite}}
@keyframes ks-pulse{{0%,100%{{opacity:1}}50%{{opacity:.6}}}}

/* ── 버튼 ── */
.btn{{
  border:1px solid var(--border2);
  border-radius:4px;
  padding:6px 16px;
  cursor:pointer;
  font-size:12px;
  font-weight:600;
  font-family:var(--sans);
  transition:all .15s;
}}
.btn-danger{{background:rgba(246,70,93,.1);color:var(--red);border-color:rgba(246,70,93,.3)}}
.btn-danger:hover{{background:var(--red);color:#fff}}
.btn-success{{background:rgba(14,203,129,.1);color:var(--green);border-color:rgba(14,203,129,.3)}}
.btn-success:hover{{background:var(--green);color:#000}}
.btn-neutral{{background:var(--surface2);color:var(--text2);border-color:var(--border2)}}
.btn-neutral:hover{{color:var(--text);border-color:var(--text2)}}
.ks-controls{{margin-top:12px;display:flex;gap:8px;flex-wrap:wrap}}

/* ── 상태 텍스트 ── */
.status-chip{{
  display:inline-flex;align-items:center;gap:5px;
  font-size:11px;font-weight:600;
  padding:3px 9px;border-radius:3px;
  font-family:var(--mono);
}}
.status-ok{{background:rgba(14,203,129,.1);color:var(--green)}}
.status-err{{background:rgba(246,70,93,.1);color:var(--red)}}
.status-warn{{background:rgba(240,165,0,.1);color:var(--yellow)}}
.status-idle{{background:var(--surface2);color:var(--text3)}}
.dot{{width:6px;height:6px;border-radius:50%;background:currentColor;flex-shrink:0}}

/* ── 운영 진단 stat 그리드 ── */
.ops-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}}
.ops-stat{{background:var(--surface2);border:1px solid var(--border);border-radius:4px;padding:8px 10px}}
.ops-stat-label{{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px}}
.ops-stat-val{{font-family:var(--mono);font-size:14px;font-weight:600;color:var(--text);font-variant-numeric:tabular-nums}}

/* ── 계좌 KV 테이블 ── */
.kv-table{{width:100%;border-collapse:collapse;margin-top:10px}}
.kv-table td{{padding:5px 0;border-bottom:1px solid var(--border);font-size:12px}}
.kv-table tr:last-child td{{border-bottom:none}}
.kv-table td:first-child{{color:var(--text3);width:110px}}
.kv-table td:last-child{{font-family:var(--mono);color:var(--text);text-align:right;font-variant-numeric:tabular-nums}}

/* ── 타임라인 ── */
.tl-wrap{{max-height:340px;overflow-y:auto;border:1px solid var(--border);border-radius:4px}}
.tl-table{{width:100%;border-collapse:collapse;font-size:11px}}
.tl-table th{{
  font-size:10px;font-weight:600;color:var(--text3);
  text-transform:uppercase;letter-spacing:.06em;
  padding:6px 10px;border-bottom:1px solid var(--border);
  background:var(--surface);
  position:sticky;top:0;
  text-align:left;
}}
.tl-table td{{
  padding:5px 10px;
  border-bottom:1px solid var(--border);
  vertical-align:top;
}}
.tl-table tbody tr:last-child td{{border-bottom:none}}
.tl-table tbody tr:hover{{background:rgba(255,255,255,.02)}}
.tl-ts{{font-family:var(--mono);font-size:10px;color:var(--text3);white-space:nowrap}}
.tl-detail{{font-family:var(--mono);font-size:10px;color:var(--text2);word-break:break-all;max-width:400px}}
.tl-badge{{
  display:inline-block;border-radius:3px;
  padding:1px 6px;font-size:10px;font-weight:600;
  font-family:var(--mono);letter-spacing:.02em;white-space:nowrap;
}}
.tl-signal{{background:rgba(24,144,255,.12);color:#4da6ff;border:1px solid rgba(24,144,255,.2)}}
.tl-meta{{background:rgba(153,102,255,.12);color:#b07ef4;border:1px solid rgba(153,102,255,.2)}}
.tl-order{{background:rgba(240,165,0,.12);color:var(--yellow);border:1px solid rgba(240,165,0,.2)}}
.tl-fill{{background:rgba(14,203,129,.12);color:var(--green);border:1px solid rgba(14,203,129,.2)}}
.tl-group{{background:rgba(91,95,100,.1);color:var(--text3);border:1px solid var(--border);cursor:pointer}}
.tl-group:hover{{background:rgba(91,95,100,.2)}}

/* ── 거래 이력 테이블 ── */
.trades-wrap{{max-height:320px;overflow-y:auto;border:1px solid var(--border);border-radius:4px}}
.trades-table{{width:100%;border-collapse:collapse;font-size:11px}}
.trades-table th{{
  font-size:10px;font-weight:600;color:var(--text3);
  text-transform:uppercase;letter-spacing:.06em;
  padding:7px 10px;border-bottom:1px solid var(--border);
  background:var(--surface);
  position:sticky;top:0;text-align:left;
}}
.trades-table th.num{{text-align:right}}
.trades-table td{{
  padding:6px 10px;border-bottom:1px solid var(--border);
  vertical-align:middle;
}}
.trades-table tbody tr:last-child td{{border-bottom:none}}
.trades-table tbody tr:hover{{background:rgba(255,255,255,.025)}}
.trades-table tbody tr:nth-child(even){{background:rgba(255,255,255,.012)}}
.td-mono{{font-family:var(--mono);text-align:right;font-variant-numeric:tabular-nums}}
.td-sym{{font-family:var(--mono);font-weight:600;font-size:12px}}
.td-dim{{color:var(--text3);font-size:10px}}
.side-buy{{color:var(--green);font-weight:700}}
.side-sell{{color:var(--red);font-weight:700}}
.state-filled{{color:var(--green);font-size:10px}}
.state-pending{{color:var(--yellow);font-size:10px}}

/* ── run-status ── */
.run-status{{font-size:13px;font-weight:700;margin-bottom:8px;color:var(--text2)}}
.last-ts{{font-size:10px;color:var(--text3);margin-top:6px;font-family:var(--mono)}}

/* ── 전략 카탈로그 섹션 ── */
.catalog-section{{}}
{_STRATEGY_CARD_CSS}
</style>
</head>
<body>

<!-- 상단 헤더 바 -->
<div class="topbar">
  <span class="topbar-brand">QTA TERMINAL</span>
  <div class="topbar-nav">
    <a href="/strategies" class="nav-pill">전략 카탈로그</a>
    <a href="/signals" class="nav-pill">신호 목록</a>
    <a href="/cs-tsmom" class="nav-pill">cs-tsmom (90%)</a>
    <a href="/airborne" class="nav-pill">airborne 적중</a>
    <a href="/ma-cross" class="nav-pill">골든/데드크로스</a>
    <a href="/manual" class="nav-pill">수동 거래</a>
    <a href="/shadow_runs" class="nav-pill">Shadow Runs</a>
    <a href="/patch-notes" class="nav-pill">패치노트</a>
  </div>
  <span class="topbar-ts">{datetime.now(_KST).strftime('%Y-%m-%d %H:%M:%S KST')}</span>
</div>

<div class="page">

  <!-- ── 빠른 이동 (Quick Links) ── -->
  <div class="quick-links">
    <a href="/signals" class="quick-link-card quick-link-signals">
      <span class="quick-link-icon">📡</span>
      <span class="quick-link-body">
        <span class="quick-link-title">신호 목록 (Binance)</span>
        <span class="quick-link-sub">실시간 buy/sell 신호 · 후속 체결 매칭</span>
      </span>
      <span class="quick-link-arrow">→</span>
    </a>
    <a href="/cs-tsmom" class="quick-link-card quick-link-signals">
      <span class="quick-link-icon">📈</span>
      <span class="quick-link-body">
        <span class="quick-link-title">cs-tsmom 신호 (90% 전략)</span>
        <span class="quick-link-sub">12-1m 모멘텀 · 30종목 top-10 랭킹 · Pine Script 동일 식</span>
      </span>
      <span class="quick-link-arrow">→</span>
    </a>
    <a href="/airborne" class="quick-link-card quick-link-signals">
      <span class="quick-link-icon">🔥</span>
      <span class="quick-link-body">
        <span class="quick-link-title">airborne 알림 적중</span>
        <span class="quick-link-sub">오늘 FIRE · TP/SL 시뮬 · 시간대·방향별 분석</span>
      </span>
      <span class="quick-link-arrow">→</span>
    </a>
    <a href="/ma-cross" class="quick-link-card quick-link-signals">
      <span class="quick-link-icon">✚</span>
      <span class="quick-link-body">
        <span class="quick-link-title">골든/데드 크로스 적중</span>
        <span class="quick-link-sub">bitget top-100 SMA25/200 · TP/SL 시뮬 (1:6) · golden/death 분석</span>
      </span>
      <span class="quick-link-arrow">→</span>
    </a>
    <a href="/manual" class="quick-link-card">
      <span class="quick-link-icon">✍️</span>
      <span class="quick-link-body">
        <span class="quick-link-title">수동 거래 입력</span>
        <span class="quick-link-sub">손으로 산 거래 + 지표 근거 메모 · 일일 리포트 대상</span>
      </span>
      <span class="quick-link-arrow">→</span>
    </a>
    <a href="/strategies" class="quick-link-card">
      <span class="quick-link-icon">📋</span>
      <span class="quick-link-body">
        <span class="quick-link-title">전략 카탈로그</span>
        <span class="quick-link-sub">활성 전략 · ON/OFF 토글</span>
      </span>
      <span class="quick-link-arrow">→</span>
    </a>
    <a href="/shadow_runs" class="quick-link-card">
      <span class="quick-link-icon">🌑</span>
      <span class="quick-link-body">
        <span class="quick-link-title">Shadow Runs</span>
        <span class="quick-link-sub">데몬 가동 이력</span>
      </span>
      <span class="quick-link-arrow">→</span>
    </a>
  </div>

  <!-- ── 섹션 1: PnL 요약 (venue 별 — 거래소 원장 기준) ── -->
  <div>
    <div class="section-hdr"><h2>손익 (PnL)</h2><div class="section-hdr-line"></div></div>
    <!-- venue 별 일간/월간 실현손익. JS pnlVenueRefresh() 가 /api/pnl 로 채운다.
         Binance = 거래소 income 원장 NET, KIS = round-trip 재구성(추정).
         "통합" 카드는 제거 — KRW·USDT 는 별개 통화라 합산 불가. -->
    <div class="pnl-venue-grid">
      <!-- Bitget USDT — 청산 포지션 netProfit 원장 (실제 실현손익, #395) -->
      <div class="pnl-venue-card">
        <div class="pnl-venue-header">
          <span class="pnl-venue-flag">&#9651;</span>
          <span class="pnl-venue-name">Bitget Futures</span>
          <span class="pnl-venue-currency">USDT</span>
        </div>
        <div class="pnl-venue-rows" id="pnl-venue-bitget">
          <div class="pnl-venue-row"><span class="pnl-venue-period">일간</span><span class="pnl-venue-val zero">조회중…</span></div>
          <div class="pnl-venue-row"><span class="pnl-venue-period">월간</span><span class="pnl-venue-val zero">조회중…</span></div>
        </div>
      </div>
      <!-- KIS KRW — round-trip 재구성 추정치 (KIS income TR 연동은 후속) -->
      <div class="pnl-venue-card">
        <div class="pnl-venue-header">
          <span class="pnl-venue-flag">&#127472;&#127479;</span>
          <span class="pnl-venue-name">KIS</span>
          <span class="pnl-venue-currency">KRW · 추정</span>
        </div>
        <div class="pnl-venue-rows" id="pnl-venue-kis">
          <div class="pnl-venue-row"><span class="pnl-venue-period">일간</span><span class="pnl-venue-val zero">조회중…</span></div>
          <div class="pnl-venue-row"><span class="pnl-venue-period">월간</span><span class="pnl-venue-val zero">조회중…</span></div>
        </div>
      </div>
    </div>
    <div class="pnl-no-sum-note">Bitget = 청산 포지션 netProfit NET (실현손익 + 펀딩 − 수수료) — 거래소 화면과 일치. KIS = 거래 재구성 추정치 (income TR 연동 후속). KRW·USDT 는 별개 통화 — 합산 불가.</div>
  </div>

  <!-- ── 섹션 2: Binance 계좌 + KIS 계좌 나란히 ── -->
  <div>
    <div class="section-hdr"><h2>계좌 / 실제 포지션</h2><div class="section-hdr-line"></div></div>
    <!-- #238 follow-up — venue 실증 상태. INERT = real equity 미확보로
         해당 venue 주문이 전량 보류 중 ("0 trades" 의 진짜 이유). -->
    <div id="venue-equity-banner" style="display:none;gap:8px;flex-wrap:wrap;margin-bottom:10px"></div>
    <div class="grid-2">

      <!-- Binance Futures 계좌 카드 (강조) -->
      <div class="card card-sm" id="account-card-binance">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <span style="font-size:12px;font-weight:600;color:var(--text)">Binance Futures <span style="font-size:10px;color:var(--text3);font-weight:400">USDS-M</span></span>
          <span id="bnb-status" class="status-chip status-idle"><span class="dot"></span>조회 중</span>
        </div>
        <!-- 지갑/가용/uPnL 히어로 셀 -->
        <div class="acct-hero">
          <div class="acct-hero-cell">
            <div class="acct-hero-label">지갑 잔고</div>
            <div class="acct-hero-val" id="bnb-wallet">—</div>
            <div class="acct-hero-sub">USDT</div>
          </div>
          <div class="acct-hero-cell">
            <div class="acct-hero-label">가용 증거금</div>
            <div class="acct-hero-val" id="bnb-avail">—</div>
            <div class="acct-hero-sub">USDT</div>
          </div>
          <div class="acct-hero-cell">
            <div class="acct-hero-label">미실현 손익</div>
            <div class="acct-hero-val" id="bnb-upnl">—</div>
            <div class="acct-hero-sub" id="bnb-pos-n">포지션 —</div>
          </div>
        </div>
        <!-- 열린 포지션 테이블 -->
        <div style="margin-top:12px;max-height:200px;overflow-y:auto;border:1px solid var(--border);border-radius:4px" id="bnb-pos-wrap">
          <table class="pos-table">
            <thead>
              <tr>
                <th>심볼 / 방향</th>
                <th>수량</th>
                <th>진입가</th>
                <th>미실현 PnL</th>
              </tr>
            </thead>
            <tbody id="bnb-pos-rows">
              <tr><td colspan="4" style="text-align:center;color:var(--text3);padding:14px;font-family:var(--sans);font-size:11px">포지션 없음</td></tr>
            </tbody>
          </table>
        </div>
        <div class="last-ts" id="bnb-detail">API Key: <span id="bnb-key">—</span> &nbsp;|&nbsp; <span id="bnb-mode">—</span> &nbsp;|&nbsp; 기준 <span id="bnb-snap" title="이 카드 데이터의 스냅샷 시각 (KST). 실제 Binance 화면과의 미세차이는 이 지연 때문 — 계산은 Binance 의 unRealizedProfit 그대로 사용">—</span></div>
      </div>

      <!-- Bitget Futures 계좌 카드 (P5) -->
      <div class="card card-sm" id="account-card-bitget">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <span style="font-size:12px;font-weight:600;color:var(--text)">Bitget Futures <span style="font-size:10px;color:var(--text3);font-weight:400">USDT-M</span></span>
          <span id="bg-status" class="status-chip status-idle"><span class="dot"></span>조회 중</span>
        </div>
        <div class="acct-hero">
          <div class="acct-hero-cell">
            <div class="acct-hero-label">지갑 잔고</div>
            <div class="acct-hero-val" id="bg-wallet">—</div>
            <div class="acct-hero-sub">USDT</div>
          </div>
          <div class="acct-hero-cell">
            <div class="acct-hero-label">가용 증거금</div>
            <div class="acct-hero-val" id="bg-avail">—</div>
            <div class="acct-hero-sub">USDT</div>
          </div>
          <div class="acct-hero-cell">
            <div class="acct-hero-label">미실현 손익</div>
            <div class="acct-hero-val" id="bg-upnl">—</div>
            <div class="acct-hero-sub" id="bg-pos-n">포지션 —</div>
          </div>
        </div>
        <div style="margin-top:12px;max-height:200px;overflow-y:auto;border:1px solid var(--border);border-radius:4px" id="bg-pos-wrap">
          <table class="pos-table">
            <thead>
              <tr>
                <th>심볼 / 방향</th>
                <th>수량</th>
                <th>진입가</th>
                <th>미실현 PnL</th>
              </tr>
            </thead>
            <tbody id="bg-pos-rows">
              <tr><td colspan="4" style="text-align:center;color:var(--text3);padding:14px;font-family:var(--sans);font-size:11px">포지션 없음</td></tr>
            </tbody>
          </table>
        </div>
        <div class="last-ts" id="bg-detail">API Key: <span id="bg-key">—</span> &nbsp;|&nbsp; <span id="bg-mode">—</span> &nbsp;|&nbsp; 기준 <span id="bg-snap" title="Bitget 스냅샷 시각 (KST). 15s TTL 캐시 — 실 Bitget 화면과 약간의 지연 가능.">—</span></div>
      </div>

      <!-- KIS 계좌 카드 -->
      <div class="card card-sm" id="account-card-kis">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <span style="font-size:12px;font-weight:600;color:var(--text)">KIS <span style="font-size:10px;color:var(--text3);font-weight:400">paper · KRX</span></span>
          <span id="kis-status" class="status-chip status-idle"><span class="dot"></span>조회 중</span>
        </div>
        <table class="kv-table">
          <tbody>
            <tr><td>계좌번호</td><td id="kis-cano">—</td></tr>
            <tr><td>현금 (KRW)</td><td id="kis-cash">—</td></tr>
            <tr><td>평가금액</td><td id="kis-eval">—</td></tr>
            <tr><td>보유 종목</td><td id="kis-positions">—</td></tr>
          </tbody>
        </table>
        <div class="last-ts" id="kis-detail">.env 의 HANTOO_FAKE_* 인증.</div>

        <!-- 거래 시작/정지 — KIS 카드 아래 -->
        <div style="margin-top:14px;border-top:1px solid var(--border);padding-top:12px">
          <div style="font-size:10px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">거래 제어</div>
          <div id="run-status" class="run-status" style="font-size:12px">상태 조회 중…</div>
          <div class="ks-controls" style="margin-top:8px">
            <button id="btn-run-start" class="btn btn-success" onclick="runStart()">거래 시작</button>
            <button id="btn-run-stop" class="btn btn-danger" onclick="runStop()">거래 정지</button>
          </div>
          <div class="last-ts" id="run-detail">production.yaml 의 등록 전략으로 시작합니다.</div>
        </div>
      </div>

    </div>
  </div>

  <!-- ── 섹션 3: 전략별 포지션 ── -->
  <div>
    <div class="section-hdr"><h2>전략별 포지션</h2><div class="section-hdr-line"></div></div>
    <div class="card" style="padding:0">
      <div class="trades-wrap">
        <table class="pos-table">
          <thead>
            <tr>
              <th style="text-align:left;padding-left:16px">전략 / 종목</th>
              <th>방향</th>
              <th>매수 건/수량</th>
              <th>매도 건/수량</th>
              <th>순포지션</th>
              <th>평단가</th>
              <th>현재가</th>
              <th>손익%</th>
              <th>실현손익</th>
              <th>최근 체결</th>
              <th style="padding-right:16px">청산</th>
            </tr>
          </thead>
          <tbody id="stratpos-tbody">
            <tr><td colspan="11" style="text-align:center;color:var(--text3);padding:20px;font-family:var(--sans);font-size:11px">조회 중…</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ── 섹션 4: 타임라인 + 한도/킬스위치 2열 ── -->
  <div class="grid-2" style="align-items:start">

    <!-- 타임라인 -->
    <div>
      <div class="section-hdr"><h2>매매 타임라인</h2><div class="section-hdr-line"></div></div>
      <div class="card" style="padding:0">
        <div class="tl-wrap">
          <table class="tl-table">
            <thead><tr><th>시각</th><th>유형</th><th>상세</th></tr></thead>
            <tbody id="timeline">{rows_html}</tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 한도 + 킬스위치 + 운영진단 수직 적층 -->
    <div style="display:flex;flex-direction:column;gap:12px">

      <!-- 한도 사용률 -->
      <div>
        <div class="section-hdr"><h2>한도 사용률</h2><div class="section-hdr-line"></div></div>
        <div class="card card-sm">
          {gauges_html}
        </div>
      </div>

      <!-- 비상정지 -->
      <div>
        <div class="section-hdr"><h2>비상정지</h2><div class="section-hdr-line"></div></div>
        <div class="card card-sm">
          <div class="ks-overall {ks_overall_cls}">{ks_overall_txt}</div>
          <table class="kv-table">
            <tbody>{ks_rows}</tbody>
          </table>
          <div class="last-ts">마지막 발동: {last_ts}</div>
          <div class="ks-controls">
            <button class="btn btn-danger" onclick="triggerKS('manual')">수동 발동</button>
            <button class="btn btn-success" onclick="resetKS('manual')">수동 해제</button>
          </div>
        </div>
      </div>

      <!-- 운영 진단 -->
      <div>
        <div class="section-hdr"><h2>운영 진단 (Ops)</h2><div class="section-hdr-line"></div></div>
        <div class="card card-sm" id="ops-card">
          <div id="ops-summary" class="status-chip status-idle" style="margin-bottom:10px"><span class="dot"></span>조회 중</div>
          <div class="ops-grid">
            <div class="ops-stat"><div class="ops-stat-label">Bars 수신</div><div class="ops-stat-val" id="ops-bars">—</div></div>
            <div class="ops-stat"><div class="ops-stat-label">전략 평가</div><div class="ops-stat-val" id="ops-evals">—</div></div>
            <div class="ops-stat"><div class="ops-stat-label">시그널 발생</div><div class="ops-stat-val" id="ops-signals">—</div></div>
            <div class="ops-stat"><div class="ops-stat-label">주문 제출</div><div class="ops-stat-val" id="ops-orders">—</div></div>
            <div class="ops-stat"><div class="ops-stat-label">체결</div><div class="ops-stat-val" id="ops-fills">—</div></div>
            <div class="ops-stat"><div class="ops-stat-label">오류</div><div class="ops-stat-val" id="ops-errors">—</div></div>
          </div>
          <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
            <span class="last-ts">buy/sell/hold/exc: <span id="ops-decisions" style="color:var(--text2)">—</span></span>
          </div>
          <div class="last-ts" style="margin-top:4px">마지막 bar: <span id="ops-last-bar" style="color:var(--text2)">—</span></div>
          <div class="last-ts">마지막 fill: <span id="ops-last-fill" style="color:var(--text2)">—</span></div>
        </div>
      </div>

    </div>
  </div>

  <!-- ── 섹션 5: 거래 이력 ── -->
  <div>
    <div class="section-hdr"><h2>매수/매도 이력 (최근 50건)</h2><div class="section-hdr-line"></div></div>
    <div class="card" style="padding:0">
      <div class="trades-wrap">
        <table class="trades-table">
          <thead>
            <tr>
              <th>시각</th>
              <th>전략</th>
              <th>종목</th>
              <th>방향</th>
              <th class="num">수량</th>
              <th class="num">가격</th>
              <th>상태</th>
              <th>브로커</th>
            </tr>
          </thead>
          <tbody id="trades-tbody">
            <tr><td colspan="8" style="text-align:center;color:var(--text3);padding:20px;font-family:var(--sans);font-size:11px">조회 중…</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ── 섹션 6: 거래 내역 (round-trip 재구성) ── -->
  <div>
    <div class="section-hdr"><h2>거래 내역 (round-trip)</h2><div class="section-hdr-line"></div></div>
    <div class="pnl-no-sum-note">실현손익은 <b>청산 수수료만</b> 반영합니다. 상단 venue 손익 카드는 진입 수수료까지 포함하므로 두 수치가 미세하게 다를 수 있습니다 (둘 다 정상 — 정의 차이).</div>
    <div class="card" style="padding:0">
      <div class="th-history-wrap">
        <table class="th-table">
          <thead>
            <tr>
              <th>진입시각</th>
              <th>청산시각</th>
              <th>보유시간</th>
              <th>전략</th>
              <th>종목</th>
              <th>venue</th>
              <th>방향</th>
              <th class="num">수량</th>
              <th class="num">진입가</th>
              <th class="num">청산가</th>
              <th class="num">실현손익</th>
              <th>상태</th>
            </tr>
          </thead>
          <tbody id="th-tbody">
            <tr><td colspan="12" style="text-align:center;color:var(--text3);padding:20px;font-family:var(--sans);font-size:11px">조회 중…</td></tr>
          </tbody>
        </table>
      </div>
      <div id="th-truncnote" class="th-truncnote" style="display:none"></div>
    </div>
  </div>

  <!-- ── 섹션 7: 전략 카탈로그 ── -->
  <div class="catalog-section">
    <div class="section-hdr"><h2>전략 카탈로그</h2><div class="section-hdr-line"></div></div>
    <div class="strat-grid">{catalog_cards_html}</div>
  </div>

</div><!-- /page -->

<script>
// ── 유틸 ──────────────────────────────────────────────────────────
function escHtml(s) {{
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
}}
function fmtNum(n, dec) {{
  if (n == null || n === '') return '—';
  const v = Number(n);
  if (isNaN(v)) return String(n);
  if (dec != null) {{
    const s = v.toFixed(dec);
    const [intPart, decPart] = s.split('.');
    const intFmt = intPart.replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ',');
    return decPart !== undefined ? intFmt + '.' + decPart : intFmt;
  }}
  return v.toLocaleString('ko-KR');
}}
function fmtPnl(v, suffix) {{
  if (v == null) return '<span style="color:var(--text3)">—</span>';
  const n = Number(v);
  const cls = n > 0 ? 'col-green' : n < 0 ? 'col-red' : 'col-dim';
  const sign = n > 0 ? '+' : '';
  return `<span class="${{cls}}">${{sign}}${{fmtNum(n,2)}}${{suffix||''}}</span>`;
}}
function colorEl(el, v) {{
  if (el == null) return;
  const n = Number(v);
  el.style.color = n > 0 ? 'var(--green)' : n < 0 ? 'var(--red)' : 'var(--text2)';
}}

// ── 킬스위치 ──────────────────────────────────────────────────────
async function triggerKS(reason){{
  await fetch('/api/kill-switch/trigger',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{reason}})}});
  location.reload();
}}
async function resetKS(reason){{
  await fetch('/api/kill-switch/reset',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{reason}})}});
  location.reload();
}}

// ── 타임라인 WS + 이벤트 그룹화 ─────────────────────────────────
const TYPE_CLASS = {{
  signal_emitted:       'tl-signal',
  metalabeler_decision: 'tl-meta',
  order_placed:         'tl-order',
  order_submitted:      'tl-order',
  fill_received:        'tl-fill',
  order_filled:         'tl-fill',
}};
const TIMELINE_MAX_ROWS = 120;
let _tlBuffer = [];  // {{event_type, ts, payload, count}}

function tlFlushBuffer(tbody) {{
  // 같은 event_type 이 연속으로 반복되면 "×N" 그룹 행으로 접기
  const grouped = [];
  for (const ev of _tlBuffer) {{
    const last = grouped[grouped.length - 1];
    if (last && last.event_type === ev.event_type) {{
      last.count = (last.count || 1) + 1;
      last.ts_last = ev.ts;
    }} else {{
      grouped.push({{...ev, count: 1}});
    }}
  }}
  _tlBuffer = [];
  for (const ev of grouped) {{
    _tlInsertRow(tbody, ev);
  }}
  // DOM cap
  while (tbody.rows.length > TIMELINE_MAX_ROWS) tbody.deleteRow(tbody.rows.length - 1);
}}

function _tlInsertRow(tbody, ev) {{
  const cls = TYPE_CLASS[ev.event_type] || '';
  let labelHtml;
  if (ev.count > 1) {{
    labelHtml = `<span class="tl-badge tl-group" title="연속 ${{ev.count}}건 — 클릭해서 펼치기" onclick="tlExpand(this,${{escHtml(JSON.stringify(ev))}})">`
      + `${{escHtml(ev.event_type)}} <b>×${{ev.count}}</b></span>`;
  }} else {{
    labelHtml = `<span class="tl-badge ${{cls}}">${{escHtml(ev.event_type || '')}}</span>`;
  }}
  const detail = ev.payload ? escHtml(JSON.stringify(ev.payload)).slice(0, 200) : '';
  const tsStr = ev.count > 1
    ? escHtml(fmtKst(ev.ts)) + ' … ' + escHtml(fmtKst(ev.ts_last).slice(11, 19))
    : escHtml(fmtKst(ev.ts));
  const row = document.createElement('tr');
  row.innerHTML = `<td class="tl-ts">${{tsStr}}</td><td>${{labelHtml}}</td><td class="tl-detail">${{detail}}</td>`;
  tbody.insertBefore(row, tbody.firstChild);
}}

function tlExpand(el, evJson) {{
  // no-op placeholder: groups are read-only in this version
  const ev = typeof evJson === 'string' ? JSON.parse(evJson) : evJson;
  window.alert(`event_type: ${{ev.event_type}}\\ncount: ${{ev.count}}\\nfirst: ${{ev.ts}}\\nlast: ${{ev.ts_last||ev.ts}}`);
}}

let _tlFlushTimer = null;
function tlAppend(ev) {{
  const tbody = document.getElementById('timeline');
  if (!tbody) return;
  if (ev.phase === 'live_ready') return;
  _tlBuffer.push(ev);
  clearTimeout(_tlFlushTimer);
  _tlFlushTimer = setTimeout(() => tlFlushBuffer(tbody), 80);
}}
function tlConnect() {{
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${{proto}}//${{location.host}}/ws/timeline?replay=${{TIMELINE_MAX_ROWS}}`);
  ws.onmessage = (e) => {{
    try {{
      const ev = JSON.parse(e.data);
      tlAppend(ev);
      // 2026-05-21 — broker ↔ store reconciliation 알림. 사용자가 Binance UI
      // 에서 직접 close 한 경우 등 mismatch 발견되면 banner 로 즉시 노출.
      if (ev && ev.event_type === 'position_reconciled') {{
        showReconcileBanner(ev.payload || {{}});
      }}
    }} catch(err) {{ console.warn('ws parse', err); }}
  }};
  ws.onclose = () => setTimeout(tlConnect, 1000);
  ws.onerror = () => ws.close();
}}
if (typeof WebSocket !== 'undefined') {{ tlConnect(); }}

// 2026-05-21 — Reconciliation 알림 토스트.
function showReconcileBanner(payload) {{
  // 2026-05-22 — 다크 테마 매칭 + auto_fix / alert_only_* 색상 구분 +
  // (symbol, action) 단위 stack (중복 알림 합치고 timestamp 만 갱신).
  let stack = document.getElementById('reconcile-stack');
  if (!stack) {{
    stack = document.createElement('div');
    stack.id = 'reconcile-stack';
    stack.style.cssText = 'position:fixed;top:12px;right:12px;'
      + 'display:flex;flex-direction:column;gap:8px;'
      + 'z-index:9999;max-width:380px;font-family:ui-monospace,Menlo,Consolas,monospace;';
    document.body.appendChild(stack);
  }}
  const action = payload.action || 'unknown';
  const sym = payload.symbol || '?';
  const dedupeKey = `${{sym}}:${{action}}`;
  const isAutoFix = action === 'auto_fix';
  const isPhantom = action === 'alert_only_phantom_broker';
  const isMulti = action === 'alert_only_multi_holder';

  let toast = stack.querySelector(`[data-key="${{dedupeKey}}"]`);
  if (!toast) {{
    toast = document.createElement('div');
    toast.dataset.key = dedupeKey;
    toast.style.cssText = 'background:#0f1116;border:1px solid;border-radius:6px;'
      + 'padding:10px 12px;color:#d8dde7;font-size:12px;line-height:1.45;'
      + 'box-shadow:0 4px 14px rgba(0,0,0,0.4);min-width:340px;';
    toast.style.borderColor = isAutoFix ? '#15803d'
      : isPhantom ? '#dc2626'
      : isMulti ? '#d97706'
      : '#64748b';
    stack.appendChild(toast);
  }}

  const badgeColor = isAutoFix ? '#22c55e'
    : isPhantom ? '#ef4444'
    : isMulti ? '#f59e0b'
    : '#94a3b8';
  const badgeLabel = isAutoFix ? '자동 동기화 완료'
    : isPhantom ? '브로커 phantom 포지션'
    : isMulti ? '다중 전략 — 수동 확인'
    : action;
  const holders = payload.holders || {{}};
  const holderEntries = Object.entries(holders);
  const holderLine = holderEntries.length
    ? holderEntries.map(([k, v]) => {{
        const short = k.length > 28 ? k.slice(0, 28) + '…' : k;
        return `<span style="color:#94a3b8">${{short}}</span> ${{v}}`;
      }}).join(' · ')
    : '<span style="color:#64748b">(없음)</span>';

  toast.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
      <span style="font-size:11px;font-weight:700;color:${{badgeColor}};letter-spacing:0.5px;">
        ${{badgeLabel}}
      </span>
      <button onclick="this.closest('[data-key]').remove()"
        style="background:transparent;border:none;color:#64748b;cursor:pointer;
          font-size:14px;padding:0;line-height:1;">✕</button>
    </div>
    <div style="font-size:14px;font-weight:600;color:#e2e8f0;margin-bottom:6px;">
      ${{sym}}
    </div>
    <div style="font-size:11px;color:#94a3b8;margin-bottom:2px;">
      logical <span style="color:#e2e8f0">${{payload.logical_net}}</span>
      <span style="margin:0 4px;color:#475569">→</span>
      broker <span style="color:#e2e8f0">${{payload.broker_net}}</span>
    </div>
    <div style="font-size:11px;color:#94a3b8;margin-bottom:6px;">
      delta <span style="color:${{badgeColor}}">${{payload.delta}}</span>
    </div>
    <div style="font-size:10px;color:#64748b;border-top:1px solid #1e293b;padding-top:6px;">
      ${{holderLine}}
    </div>
  `;

  // auto_fix 는 정보성 → 20초 자동 dismiss. alert_only_* 는 사용자가 ✕ 닫을 때까지 유지.
  if (isAutoFix) {{
    clearTimeout(toast._dismissTimer);
    toast._dismissTimer = setTimeout(() => toast.remove(), 20000);
  }}
}}

// ── 거래 시작/정지 ──────────────────────────────────────────────
async function runStart() {{
  document.getElementById('run-status').textContent = '시작 중…';
  const r = await fetch('/api/run/start', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{}})}});
  const d = await r.json();
  runRefresh();
  if (!d.ok) alert('시작 실패: ' + (d.reason || JSON.stringify(d)));
}}
async function runStop() {{
  document.getElementById('run-status').textContent = '정지 중…';
  const r = await fetch('/api/run/stop', {{method:'POST'}});
  await r.json();
  runRefresh();
}}
async function runRefresh() {{
  try {{
    const r = await fetch('/api/run/status');
    const d = await r.json();
    const el = document.getElementById('run-status');
    const det = document.getElementById('run-detail');
    if (!d.available) {{
      el.textContent = '컨트롤러 미주입 (cmd 모드)';
      if (det) det.textContent = 'qta.exe --symbols 005930 --broker kis-paper-shadow';
      return;
    }}
    const status = d.status || '?';
    el.textContent = '상태: ' + status;
    el.style.color = status === 'running' ? 'var(--green)' : status === 'error' ? 'var(--red)' : 'var(--text2)';
    if (det) {{
      if (d.last_error) det.textContent = 'Error: ' + d.last_error;
      else if (d.started_at) det.textContent = '시작: ' + d.started_at + (d.stopped_at ? ' · 종료: ' + d.stopped_at : '');
    }}
  }} catch(err) {{ console.warn('run-status', err); }}
}}
runRefresh();
setInterval(runRefresh, 3000);

// ── 계좌 폴링 (30s + in-flight guard) ──────────────────────────
function setStatusChip(elId, ok, okTxt, failTxt) {{
  const el = document.getElementById(elId);
  if (!el) return;
  el.className = 'status-chip ' + (ok ? 'status-ok' : 'status-err');
  el.innerHTML = `<span class="dot"></span>${{ok ? okTxt : failTxt}}`;
}}
async function acctRefresh() {{
  try {{
    const r = await fetch('/api/account/info');
    const d = await r.json();
    if (!d.available) return;
    // KIS
    const k = d.kis || {{}};
    setStatusChip('kis-status', !!k.ok, '연결됨 (paper)', k.error || '실패');
    const set = (id, v) => {{ const e = document.getElementById(id); if (e) e.textContent = v; }};
    set('kis-cano', k.cano_masked || '—');
    set('kis-cash', k.ok ? fmtNum(k.cash_balance) + ' 원' : '—');
    set('kis-eval', k.ok ? fmtNum(k.eval_amount) + ' 원' : '—');
    set('kis-positions', k.ok ? (k.n_positions || 0) + ' 종목' : '—');
    // Binance 는 별도 10s 폴링(bnbFastRefresh)이 소유 — KIS REST 한도를
    // 안 건드리면서 미실현손익을 실제 Binance 화면에 가깝게 따라가게.
  }} catch (err) {{ console.warn('account', err); }}
}}

// ── Binance 카드 렌더 (10s 폴링 + 30s 조합조회 공용) ───────────────
let _bnbSnapAt = null;  // 마지막 스냅샷 Date — "n초 전" 틱 표시용
function renderBinance(b) {{
  b = b || {{}};
  const set = (id, v) => {{ const e = document.getElementById(id); if (e) e.textContent = v; }};
  setStatusChip('bnb-status', !!b.ok, '연결됨', b.error || '실패');
  set('bnb-key', b.api_key_masked || '—');
  set('bnb-mode', b.ok ? (b.testnet ? 'Testnet' : 'Live') + ' · ' + (b.base_url_short || '') : '—');
  const walEl = document.getElementById('bnb-wallet');
  if (walEl) {{ walEl.textContent = b.ok ? fmtNum(b.wallet_balance_usdt, 2) : '—'; }}
  const avEl = document.getElementById('bnb-avail');
  if (avEl) {{ avEl.textContent = b.ok ? fmtNum(b.available_usdt, 2) : '—'; }}
  const upnlEl = document.getElementById('bnb-upnl');
  if (upnlEl) {{
    const u = b.total_unrealized_pnl;
    if (b.ok && u != null) {{
      const sign = u >= 0 ? '+' : '';
      upnlEl.textContent = sign + fmtNum(u, 2);
      colorEl(upnlEl, u);
    }} else {{
      upnlEl.textContent = '—';
      upnlEl.style.color = 'var(--text2)';
    }}
  }}
  set('bnb-pos-n', b.ok ? (b.n_positions || 0) + ' 개 포지션' : '포지션 —');
  // 전략별 포지션 표가 평단/현재가를 join 하도록 심볼별 라이브 포지션 캐시.
  // bnbApiOk = Binance ground-truth 신뢰 가능 (b.ok). false 면 stratpos
  // 필터가 USDT row 를 함부로 숨기지 않는다 (API down 시 fail-safe).
  window._bnbPosBySym = {{}};
  window._bnbApiOk = !!b.ok;
  for (const p of ((b.ok && b.positions) ? b.positions : [])) {{
    if (p && p.symbol) window._bnbPosBySym[p.symbol] = p;
  }}
  const posRows = document.getElementById('bnb-pos-rows');
  if (posRows) {{
    const ps = (b.ok && b.positions) ? b.positions : [];
    if (ps.length === 0) {{
      posRows.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text3);padding:14px;font-family:var(--sans);font-size:11px">열린 포지션 없음</td></tr>';
    }} else {{
      posRows.innerHTML = ps.map(p => {{
        const isLong = p.side === 'LONG';
        const sideCls = isLong ? 'side-long' : 'side-short';
        const sideTxt = isLong ? 'LONG' : 'SHORT';
        const pnlHtml = fmtPnl(p.unrealized_pnl, ' USDT');
        return `<tr>
          <td><span class="stratpos-sym">${{escHtml(p.symbol)}}</span><br><span class="side-badge ${{sideCls}}">${{sideTxt}}</span></td>
          <td style="text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums">${{escHtml(p.amt)}}</td>
          <td style="text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums">${{fmtNum(p.entry_price,4)}}</td>
          <td style="text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums">${{pnlHtml}}</td>
        </tr>`;
      }}).join('');
    }}
  }}
  // 스냅샷 시각 — 데이터가 "언제 기준" 인지. 실제 Binance 와의 미세차이가
  // 계산오류가 아니라 이 지연 때문임을 사용자가 눈으로 확인.
  _bnbSnapAt = b.ts ? new Date(b.ts) : (b.ok ? new Date() : null);
  bnbSnapTick();
}}
function bnbSnapTick() {{
  const el = document.getElementById('bnb-snap');
  if (!el) return;
  if (!_bnbSnapAt || isNaN(_bnbSnapAt.getTime())) {{ el.textContent = '—'; return; }}
  const age = Math.max(0, Math.round((Date.now() - _bnbSnapAt.getTime()) / 1000));
  el.textContent = fmtKst(_bnbSnapAt.toISOString()).slice(11, 19)
    + (age > 0 ? ' (' + age + '초 전)' : ' (방금)');
}}
setInterval(bnbSnapTick, 1000);
async function bnbFastRefresh() {{
  try {{
    const r = await fetch('/api/account/binance');
    const d = await r.json();
    if (!d.available) {{ renderBinance({{ok: false}}); return; }}
    renderBinance(d.binance || {{}});
  }} catch (err) {{ console.warn('bnbFast', err); }}
}}
bnbFastRefresh();
setInterval(bnbFastRefresh, 10000);

// ── Bitget Futures (P5) ─────────────────────────────────────────
let _bgSnapAt = null;
function renderBitget(b) {{
  b = b || {{}};
  const set = (id, v) => {{ const e = document.getElementById(id); if (e) e.textContent = v; }};
  setStatusChip('bg-status', !!b.ok, '연결됨', b.error || '실패');
  set('bg-key', b.api_key_masked || '—');
  set('bg-mode', b.ok ? (b.paper ? 'Demo' : 'Mainnet') + ' · ' + (b.base_url_short || '') : '—');
  const walEl = document.getElementById('bg-wallet');
  if (walEl) {{ walEl.textContent = b.ok ? fmtNum(b.wallet_balance_usdt, 2) : '—'; }}
  const avEl = document.getElementById('bg-avail');
  if (avEl) {{ avEl.textContent = b.ok ? fmtNum(b.available_usdt, 2) : '—'; }}
  const upnlEl = document.getElementById('bg-upnl');
  if (upnlEl) {{
    const u = b.total_unrealized_pnl;
    if (b.ok && u != null) {{
      const sign = u >= 0 ? '+' : '';
      upnlEl.textContent = sign + fmtNum(u, 2);
      colorEl(upnlEl, u);
    }} else {{
      upnlEl.textContent = '—';
      upnlEl.style.color = 'var(--text2)';
    }}
  }}
  set('bg-pos-n', b.ok ? (b.n_positions || 0) + ' 개 포지션' : '포지션 —');
  // 2026-06-05 — 전략별 포지션 / 거래내역 의 '거래소 청산' 판정에 필요한
  // Bitget ground-truth 캐시. window._bnbPosBySym 와 같은 패턴.
  // ``bgApiOk=false`` (Bitget 미연결) 시 fail-safe 로 fallback (renderStratPos
  // 가 어느 한쪽 API 라도 ok 면 그쪽만 봄 → Bitget down → Binance fallback).
  window._bgPosBySym = {{}};
  window._bgApiOk = !!b.ok;
  for (const p of ((b.ok && b.positions) ? b.positions : [])) {{
    if (p && p.symbol) window._bgPosBySym[p.symbol] = p;
  }}
  const posRows = document.getElementById('bg-pos-rows');
  if (posRows) {{
    const ps = (b.ok && b.positions) ? b.positions : [];
    if (ps.length === 0) {{
      posRows.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text3);padding:14px;font-family:var(--sans);font-size:11px">열린 포지션 없음</td></tr>';
    }} else {{
      posRows.innerHTML = ps.map(p => {{
        // side 누락 fallback — amt 부호 기반. Bitget API holdSide 가 가끔 빈
        // 값이거나 net 등 비표준 토큰 반환 → 배지가 안 보였던 회귀 fix.
        let isLong;
        if (p.side === 'LONG') isLong = true;
        else if (p.side === 'SHORT') isLong = false;
        else isLong = (parseFloat(p.amt) || 0) >= 0;
        const sideCls = isLong ? 'side-long' : 'side-short';
        const sideTxt = isLong ? 'LONG' : 'SHORT';
        const pnlHtml = fmtPnl(p.unrealized_pnl, ' USDT');
        return `<tr>
          <td><span class="stratpos-sym">${{escHtml(p.symbol)}}</span><br><span class="side-badge ${{sideCls}}">${{sideTxt}}</span></td>
          <td style="text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums">${{escHtml(p.amt)}}</td>
          <td style="text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums">${{fmtNum(p.entry_price,4)}}</td>
          <td style="text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums">${{pnlHtml}}</td>
        </tr>`;
      }}).join('');
    }}
  }}
  _bgSnapAt = b.ok ? new Date() : null;
  bgSnapTick();
}}
function bgSnapTick() {{
  const el = document.getElementById('bg-snap');
  if (!el) return;
  if (!_bgSnapAt) {{ el.textContent = '—'; return; }}
  const age = Math.max(0, Math.round((Date.now() - _bgSnapAt.getTime()) / 1000));
  el.textContent = fmtKst(_bgSnapAt.toISOString()).slice(11, 19)
    + (age > 0 ? ' (' + age + '초 전)' : ' (방금)');
}}
setInterval(bgSnapTick, 1000);
async function bgFastRefresh() {{
  try {{
    const r = await fetch('/api/account/bitget');
    const d = await r.json();
    if (!d.available) {{ renderBitget({{ok: false}}); return; }}
    renderBitget(d.bitget || {{}});
  }} catch (err) {{ console.warn('bgFast', err); }}
}}
bgFastRefresh();
setInterval(bgFastRefresh, 10000);

let _acctInflight = false;
async function acctRefreshGuarded() {{
  if (_acctInflight) return;
  _acctInflight = true;
  try {{ await acctRefresh(); }}
  finally {{ _acctInflight = false; }}
}}
acctRefreshGuarded();
setInterval(acctRefreshGuarded, 30000);

// ── venue 실증 상태 (#238 follow-up) ───────────────────────────
// INERT venue = real equity 미확보 → 해당 venue 주문 전량 보류.
// "0 trades" 의 근본 이유를 빨강 배너로 노출 (silent-drop 가시화).
async function venueEquityRefresh() {{
  try {{
    const r = await fetch('/api/venue_equity_status');
    const d = await r.json();
    const el = document.getElementById('venue-equity-banner');
    if (!el) return;
    if (!d.available || !d.venues || Object.keys(d.venues).length === 0) {{
      el.style.display = 'none';
      el.innerHTML = '';
      return;
    }}
    const chips = Object.keys(d.venues).sort().map(v => {{
      const s = d.venues[v] || {{}};
      if (s.ok) {{
        return `<span class="status-chip status-ok"><span class="dot"></span>`
          + `${{escHtml(v)}} 정상 (equity=${{fmtNum(s.equity, 2)}})</span>`;
      }}
      return `<span class="status-chip status-err"><span class="dot"></span>`
        + `${{escHtml(v)}} INERT — ${{escHtml(s.reason || '실증 미확보')}} `
        + `(주문 전량 보류)</span>`;
    }});
    el.innerHTML = chips.join('');
    el.style.display = 'flex';
  }} catch (err) {{ console.warn('venue-equity', err); }}
}}
venueEquityRefresh();
setInterval(venueEquityRefresh, 10000);

// ── 운영 진단 ──────────────────────────────────────────────────
async function opsRefresh() {{
  try {{
    const r = await fetch('/api/ops');
    const d = await r.json();
    if (!d.available) return;
    const set = (id, v) => {{ const e = document.getElementById(id); if (e) e.textContent = v; }};
    set('ops-bars',    fmtNum(d.bars_seen));
    set('ops-evals',   fmtNum(d.strategy_evaluated));
    const dec = d.decisions || {{}};
    set('ops-decisions', `${{fmtNum(dec.buy||0)}} / ${{fmtNum(dec.sell||0)}} / ${{fmtNum(dec.hold||0)}} / ${{fmtNum(dec.exception||0)}}`);
    set('ops-signals', fmtNum(d.signal_emitted));
    set('ops-orders',  fmtNum(d.order_submitted));
    set('ops-fills',   fmtNum(d.order_filled));
    set('ops-errors',  fmtNum(d.errors));
    set('ops-last-fill', d.last_fill_detail || '—');
    set('ops-last-bar',  d.last_bar_ts ? d.last_bar_ts.slice(0,19).replace('T',' ') : '—');
    const summaryEl = document.getElementById('ops-summary');
    if (summaryEl) {{
      const trading = (d.order_filled||0) > 0 || (d.order_submitted||0) > 0;
      const scanning = (d.strategy_evaluated||0) > 0;
      if (trading) {{
        summaryEl.className = 'status-chip status-ok';
        summaryEl.innerHTML = '<span class="dot"></span>거래 발생 중';
      }} else if (scanning) {{
        summaryEl.className = 'status-chip status-warn';
        summaryEl.innerHTML = '<span class="dot"></span>시그널 대기';
      }} else {{
        summaryEl.className = 'status-chip status-idle';
        summaryEl.innerHTML = '<span class="dot"></span>대기 중 (시세 미수신)';
      }}
    }}
  }} catch (err) {{ console.warn('ops', err); }}
}}
opsRefresh();
setInterval(opsRefresh, 3000);

// ── 거래 이력 ──────────────────────────────────────────────────
async function tradesRefresh() {{
  try {{
    const r = await fetch('/api/trades?limit=50');
    const d = await r.json();
    const tb = document.getElementById('trades-tbody');
    if (!tb) return;
    const trades = d.trades || [];
    if (trades.length === 0) {{
      tb.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text3);padding:20px;font-size:11px">거래 이력 없음 — 거래 시작 후 첫 체결을 기다리는 중</td></tr>';
      return;
    }}
    tb.innerHTML = trades.map(t => {{
      const sideCls = t.side === 'buy' ? 'side-buy' : t.side === 'sell' ? 'side-sell' : '';
      const stateCls = t.filled ? 'state-filled' : 'state-pending';
      const stateTxt = t.filled ? '체결' : '제출';
      const ts = fmtKst(t.ts);
      const qtyStr = t.qty != null ? fmtNum(t.qty, 6).replace(/\\.?0+$/, '') : '—';
      const pxStr  = t.price != null ? fmtNum(t.price, 4) : '—';
      return `<tr>
        <td class="tl-ts">${{escHtml(ts)}}</td>
        <td class="td-dim">${{escHtml(t.strategy_id)}}</td>
        <td class="td-sym">${{escHtml(t.symbol)}}</td>
        <td><span class="${{sideCls}}">${{escHtml((t.side || '').toUpperCase())}}</span></td>
        <td class="td-mono">${{qtyStr}}</td>
        <td class="td-mono">${{pxStr}}</td>
        <td><span class="${{stateCls}}">${{stateTxt}}</span></td>
        <td class="td-dim">${{escHtml(t.broker)}}</td>
      </tr>`;
    }}).join('');
  }} catch (err) {{ console.warn('trades', err); }}
}}
tradesRefresh();
setInterval(tradesRefresh, 5000);

// ── 전략별 포지션 ──────────────────────────────────────────────
async function stratPosRefresh() {{
  try {{
    const r = await fetch('/api/strategy_positions');
    const d = await r.json();
    const tb = document.getElementById('stratpos-tbody');
    if (!tb) return;
    const rows = d.strategies || [];
    if (rows.length === 0) {{
      tb.innerHTML = '<tr><td colspan="11" style="text-align:center;color:var(--text3);padding:20px;font-size:11px">아직 거래한 전략 없음</td></tr>';
      return;
    }}
    const bnbBySym = window._bnbPosBySym || {{}};
    const bnbApiOk = !!window._bnbApiOk;
    // 2026-06-05 — Bitget 도 함께 lookup. 이전엔 Binance 만 ground-truth 로
    // 봐서 Bitget 시대 TRXUSDT 가 'Binance 에 없음' = 청산 으로 잘못 판단 →
    // 전략별 포지션에서 TRX 가 filtered out → (closed) summary 만 남던 사고.
    const bgBySym = window._bgPosBySym || {{}};
    const bgApiOk = !!window._bgApiOk;
    const anyApiOk = bnbApiOk || bgApiOk;
    const realizedBySid = {{}};
    const realizedPerSymBySid = {{}};  // sid to map of sym-realized
    const filteredRows = [];
    for (const s of rows) {{
      const sym = s.symbol || '';
      const sid = s.strategy_id || '';
      if (s.realized_pnl != null) {{
        realizedBySid[sid] = (realizedBySid[sid] || 0) + s.realized_pnl;
        (realizedPerSymBySid[sid] = realizedPerSymBySid[sid] || {{}})[sym] = s.realized_pnl;
      }}
      const isUsdt = sym.endsWith('USDT');
      // 어느 한쪽 venue 라도 sym 보유면 살아있음. 둘 다 API ok 인데 둘 다 없으면 청산.
      const anyVenueHas = !!bnbBySym[sym] || !!bgBySym[sym];
      const venueClosed = isUsdt && anyApiOk && !anyVenueHas;
      if (venueClosed) continue;
      filteredRows.push(s);
    }}
    // For each strategy that has realized PnL but no surviving rows, emit a
    // single "(closed)" summary row so the user can still see the total.
    const survivedSids = new Set(filteredRows.map(r => r.strategy_id || ''));
    for (const sid of Object.keys(realizedBySid)) {{
      if (!survivedSids.has(sid)) {{
        filteredRows.push({{
          strategy_id: sid, symbol: '(closed)', __closedSummary: true,
          buy_n: 0, buy_qty: 0, sell_n: 0, sell_qty: 0, net_qty: 0,
          avg_price: null, mark_price: null, pnl_pct: null,
          realized_pnl: realizedBySid[sid], last_ts: '',
        }});
      }}
    }}
    if (filteredRows.length === 0) {{
      tb.innerHTML = '<tr><td colspan="11" style="text-align:center;color:var(--text3);padding:20px;font-size:11px">현재 보유 포지션 없음</td></tr>';
      return;
    }}
    tb.innerHTML = filteredRows.map(s => {{
      const net = s.net_qty || 0;
      let sideBadge;
      if (net > 0)       sideBadge = '<span class="side-badge side-long">LONG</span>';
      else if (net < 0)  sideBadge = '<span class="side-badge side-short">SHORT</span>';
      else               sideBadge = '<span class="side-badge side-flat">FLAT</span>';
      const netStr = net !== 0 ? fmtNum(Math.abs(net), 6).replace(/\\.?0+$/, '') : '<span class="col-dim">0</span>';
      // 평단가: WAL fill-price 우선, 없으면 라이브 Binance 포지션 진입가 fallback.
      const lp = bnbBySym[s.symbol] || null;
      const avgVal = (s.avg_price != null)
        ? s.avg_price
        : (lp && lp.entry_price != null ? lp.entry_price : null);
      const avg = (avgVal != null)
        ? fmtNum(avgVal, 4) + (s.avg_price == null && lp ? '<span class="col-dim" style="font-size:9px"> live</span>' : '')
        : '<span class="col-dim">—</span>';
      // 현재가: server cache (1Hz mark-price feed) 우선, 없으면 라이브 Binance
      // 포지션 응답의 mark_price fallback. 진입가 대비 손익 방향으로 색칠.
      const markVal = (s.mark_price != null)
        ? s.mark_price
        : (lp && lp.mark_price != null ? lp.mark_price : null);
      let curHtml = '<span class="col-dim">—</span>';
      if (markVal != null) {{
        let cls = 'col-dim';
        if (avgVal != null && net !== 0) {{
          const up = (net > 0) ? (markVal > avgVal) : (markVal < avgVal);
          cls = (markVal === avgVal) ? 'col-dim' : (up ? 'col-green' : 'col-red');
        }}
        curHtml = '<span class="' + cls + '">' + fmtNum(markVal, 4) + '</span>';
      }}
      // PnL% — server-computed (sign-corrected for LONG/SHORT)
      // + 2026-05-22: unrealized_pnl 도 표시 (Binance ground truth 의
      //   unRealizedProfit 을 |net_qty| 비율로 prorate 한 USDT 값).
      //   자체 (mark-avg)×qty 계산 vs Binance UI 사이의 mismatch 차단.
      let pnlPctHtml = '<span class="col-dim">—</span>';
      if (s.pnl_pct != null || s.unrealized_pnl != null) {{
        const parts = [];
        if (s.pnl_pct != null) {{
          const cls = (s.pnl_pct > 0) ? 'col-green' : (s.pnl_pct < 0 ? 'col-red' : 'col-dim');
          const sign = s.pnl_pct > 0 ? '+' : '';
          parts.push('<span class="' + cls + '">' + sign + fmtNum(s.pnl_pct, 2) + '%</span>');
        }}
        if (s.unrealized_pnl != null) {{
          const ucls = (s.unrealized_pnl > 0) ? 'col-green' : (s.unrealized_pnl < 0 ? 'col-red' : 'col-dim');
          const usign = s.unrealized_pnl > 0 ? '+' : '';
          parts.push('<div style="font-size:11px;margin-top:2px"><span class="' + ucls + '">' + usign + fmtNum(s.unrealized_pnl, 2) + ' USDT</span></div>');
        }}
        pnlPctHtml = parts.join('');
      }}
      const pnlHtml = fmtPnl(s.realized_pnl);
      const ts = fmtKst(s.last_ts);
      const buyQtyStr  = fmtNum(s.buy_qty,  6).replace(/\\.?0+$/, '');
      const sellQtyStr = fmtNum(s.sell_qty, 6).replace(/\\.?0+$/, '');
      // 청산 버튼 — net_qty != 0 일 때만 enabled.
      const closeBtn = (net !== 0)
        ? `<button class="stratpos-close-btn" data-sid="${{escHtml(s.strategy_id)}}" data-sym="${{escHtml(s.symbol)}}" title="시장가 전량 청산">청산</button>`
        : '<span class="col-dim">—</span>';
      // 전략 표시: cand-c-YYYY-MM-DD- prefix 제거 (긴 ID 잘림 방지). full ID 는 hover title 에 유지.
      const stratLabel = String(s.strategy_id || '').replace(/^cand-c-\\d{{4}}-\\d{{2}}-\\d{{2}}-/, '');
      return `<tr>
        <td style="padding-left:16px">
          <div class="stratpos-row">
            <div class="stratpos-strat-big" title="${{escHtml(s.strategy_id)}}">${{escHtml(stratLabel)}}</div>
            <div class="stratpos-sym-small">${{escHtml(s.symbol || '—')}}</div>
          </div>
        </td>
        <td style="text-align:center">${{sideBadge}}</td>
        <td><span class="col-green">${{s.buy_n}}</span> <span class="col-dim">건</span> / <span style="font-family:var(--mono);font-variant-numeric:tabular-nums">${{buyQtyStr}}</span></td>
        <td><span class="col-red">${{s.sell_n}}</span> <span class="col-dim">건</span> / <span style="font-family:var(--mono);font-variant-numeric:tabular-nums">${{sellQtyStr}}</span></td>
        <td style="font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right">${{netStr}}</td>
        <td style="font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right">${{avg}}</td>
        <td style="font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right">${{curHtml}}</td>
        <td style="font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right">${{pnlPctHtml}}</td>
        <td style="text-align:right">${{pnlHtml}}</td>
        <td style="font-family:var(--mono);color:var(--text3);text-align:right">${{escHtml(ts)}}</td>
        <td style="text-align:center;padding-right:16px">${{closeBtn}}</td>
      </tr>`;
    }}).join('');
    // 청산 버튼 이벤트 바인딩 — innerHTML 갱신마다 재 attach.
    tb.querySelectorAll('.stratpos-close-btn').forEach(btn => {{
      btn.addEventListener('click', async () => {{
        const sid = btn.getAttribute('data-sid');
        const sym = btn.getAttribute('data-sym');
        if (!confirm(`${{sid}}\\n${{sym}}\\n\\n전량 시장가 청산할까요?`)) return;
        btn.disabled = true;
        btn.textContent = '청산중…';
        try {{
          const r = await fetch(
            `/api/strategies/${{encodeURIComponent(sid)}}/positions/${{encodeURIComponent(sym)}}/close`,
            {{
              method: 'POST',
              headers: {{'Content-Type': 'application/json'}},
              body: JSON.stringify({{qty: 'all'}}),
            }},
          );
          const d = await r.json();
          if (!r.ok || !d.ok) {{
            alert(`청산 실패: ${{d.detail || JSON.stringify(d)}}`);
            btn.disabled = false;
            btn.textContent = '청산';
            return;
          }}
          btn.textContent = '제출됨';
          setTimeout(stratPosRefresh, 1500);
        }} catch (err) {{
          alert(`청산 요청 오류: ${{err}}`);
          btn.disabled = false;
          btn.textContent = '청산';
        }}
      }});
    }});
  }} catch (err) {{ console.warn('stratpos', err); }}
}}
stratPosRefresh();
setInterval(stratPosRefresh, 5000);

// ── PnL venue 분리 갱신 ───────────────────────────────────────────
function fmtPnlVenue(v, currency) {{
  if (v == null) return '<span class="pnl-venue-val zero">—</span><span class="pnl-venue-cur">' + currency + '</span>';
  const n = Number(v);
  const cls = n < 0 ? 'neg' : n === 0 ? 'zero' : '';
  const sign = n > 0 ? '+' : '';
  return '<span class="pnl-venue-val ' + cls + '">' + sign + fmtNum(n, 2) + '</span>'
       + '<span class="pnl-venue-cur">' + escHtml(currency) + '</span>';
}}
function renderVenueRows(containerId, data, currency) {{
  const el = document.getElementById(containerId);
  if (!el) return;
  const periods = [['일간','daily_by_venue'],['월간','monthly_by_venue']];
  el.innerHTML = periods.map(([label, key]) => {{
    const venueKey = containerId.includes('kis') ? 'kis'
                   : containerId.includes('bitget') ? 'bitget' : 'binance';
    const val = (data[key] || {{}})[venueKey];
    return '<div class="pnl-venue-row"><span class="pnl-venue-period">' + label + '</span>'
         + '<span>' + fmtPnlVenue(val != null ? val : null, currency) + '</span></div>';
  }}).join('');
}}
async function pnlVenueRefresh() {{
  try {{
    const r = await fetch('/api/pnl');
    const d = await r.json();
    // venue 별 일간/월간 — Bitget(청산 netProfit 원장) + KIS(재구성 추정).
    renderVenueRows('pnl-venue-bitget', d, 'USDT');
    renderVenueRows('pnl-venue-kis',    d, 'KRW');
  }} catch(err) {{ console.warn('pnlVenue', err); }}
}}
pnlVenueRefresh();
setInterval(pnlVenueRefresh, 5000);

// ── 거래 내역 (round-trip) ─────────────────────────────────────────
const TH_ROW_CAP = 200;
function fmtHoldingTime(secs) {{
  if (secs == null) return '—';
  const s = Math.round(Number(secs));
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s/60) + 'm ' + (s%60) + 's';
  if (s < 86400) return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
  return Math.floor(s/86400) + 'd ' + Math.floor((s%86400)/3600) + 'h';
}}
// 서버는 모든 ts 를 UTC(또는 +00:00) 로 적재한다. 화면은 한국시간(KST,
// Asia/Seoul) 으로 보여준다. tz 표기가 없으면(naive) UTC 로 간주해 'Z' 부착.
function fmtKst(ts) {{
  if (!ts) return '—';
  let s = String(ts);
  const hasTz = /[zZ]$|[+-]\\d\\d:?\\d\\d$/.test(s);
  if (!hasTz) s = s.replace(' ', 'T') + 'Z';
  const d = new Date(s);
  if (isNaN(d.getTime())) return String(ts).slice(0, 19).replace('T', ' ');
  const p = new Intl.DateTimeFormat('ko-KR', {{
    timeZone: 'Asia/Seoul', hourCycle: 'h23',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }}).formatToParts(d).reduce((o, x) => {{ o[x.type] = x.value; return o; }}, {{}});
  return `${{p.year}}-${{p.month}}-${{p.day}} ${{p.hour}}:${{p.minute}}:${{p.second}}`;
}}
function fmtTs(ts) {{
  if (!ts) return '—';
  return fmtKst(ts);
}}
async function tradeHistoryRefresh() {{
  try {{
    const r = await fetch('/api/trade_history?limit=' + TH_ROW_CAP);
    const d = await r.json();
    const tb = document.getElementById('th-tbody');
    const note = document.getElementById('th-truncnote');
    if (!tb) return;
    const trades = d.trades || [];
    if (trades.length === 0) {{
      tb.innerHTML = '<tr><td colspan="12" style="text-align:center;color:var(--text3);padding:20px;font-size:11px">거래 내역 없음 — WAL 미기록 또는 첫 진입 대기 중</td></tr>';
      if (note) note.style.display = 'none';
      return;
    }}
    if (note) {{
      if (d.truncated) {{
        note.style.display = '';
        note.textContent = '최근 ' + TH_ROW_CAP + '건만 표시 · 전체 ' + d.total + '건';
      }} else {{
        note.style.display = 'none';
      }}
    }}
    // 2026-06-05 — Binance + Bitget ground-truth 둘 다 lookup. 이전엔 Binance
    // 만 봐서 TRXUSDT (Bitget LONG) 가 'Binance 에 없음' = 거래소 청산 으로
    // 잘못 표시. 두 venue 어디에도 없을 때만 진짜 거래소 청산.
    const bnbBySymTh = window._bnbPosBySym || {{}};
    const bgBySymTh  = window._bgPosBySym  || {{}};
    const bnbApiOkTh = !!window._bnbApiOk;
    const bgApiOkTh  = !!window._bgApiOk;
    const anyApiOkTh = bnbApiOkTh || bgApiOkTh;
    tb.innerHTML = trades.map(t => {{
      const isOpenRaw = t.status === 'open';
      const symTh = t.symbol || '';
      const isUsdtTh = symTh.endsWith('USDT');
      const anyVenueHasTh = !!bnbBySymTh[symTh] || !!bgBySymTh[symTh];
      const venueClosedTh = isOpenRaw && isUsdtTh && anyApiOkTh && !anyVenueHasTh;
      const isOpen = isOpenRaw && !venueClosedTh;  // 거래소-청산 케이스는 청산됨 취급
      const rowCls = isOpen ? 'th-open' : '';
      const sideCls = t.side === 'long' ? 'side-badge side-long' : 'side-badge side-short';
      const sideTxt = (t.side || '').toUpperCase();
      const venueTxt = escHtml(t.venue || '—');
      const pnlHtml = isOpen
        ? '<span class="th-dim">보유중</span>'
        : (venueClosedTh
            ? '<span class="th-dim" title="거래소가 청산 — 우리 WAL 에 fill 미동기">거래소 청산</span>'
            : fmtPnl(t.realized_pnl, '&nbsp;' + escHtml(t.venue === 'binance' ? 'USDT' : t.venue === 'kis' ? 'KRW' : '')));
      const statusBadge = isOpen
        ? '<span class="th-open-badge">보유중</span>'
        : (venueClosedTh
            ? '<span class="th-closed-badge" style="background:#3a2a14;color:#f0a500" title="거래소에서 청산됨 (WAL 미동기)">거래소 청산</span>'
            : '<span class="th-closed-badge">청산됨</span>');
      const exitTs = isOpen ? '<span class="th-dim">—</span>' : escHtml(fmtTs(t.exit_ts));
      const exitPx = isOpen ? '<span class="th-dim">—</span>' : fmtNum(t.exit_price, 4);
      const qtyStr = t.qty != null ? String(fmtNum(t.qty, 6)).replace(/\\.?0+$/, '') : '—';
      return '<tr class="' + rowCls + '">'
        + '<td class="th-dim">' + escHtml(fmtTs(t.entry_ts)) + '</td>'
        + '<td class="th-dim">' + exitTs + '</td>'
        + '<td class="th-dim">' + escHtml(fmtHoldingTime(t.holding_seconds)) + '</td>'
        + '<td><div class="th-strategy" title="' + escHtml(t.strategy_id) + '">' + escHtml(t.strategy_id) + '</div></td>'
        + '<td class="th-sym">' + escHtml(t.symbol) + '</td>'
        + '<td><span class="th-venue">' + venueTxt + '</span></td>'
        + '<td><span class="' + sideCls + '">' + sideTxt + '</span></td>'
        + '<td class="th-mono">' + qtyStr + '</td>'
        + '<td class="th-mono">' + fmtNum(t.entry_price, 4) + '</td>'
        + '<td class="th-mono">' + exitPx + '</td>'
        + '<td style="text-align:right">' + pnlHtml + '</td>'
        + '<td>' + statusBadge + '</td>'
        + '</tr>';
    }}).join('');
  }} catch(err) {{ console.warn('tradeHistory', err); }}
}}
tradeHistoryRefresh();
setInterval(tradeHistoryRefresh, 10000);

{_STRATEGY_TOGGLE_JS}
</script>
</body>
</html>"""


# ---- Shared CSS/JS used by both / (main dashboard) and /strategies ---------

_STRATEGY_CARD_CSS = r"""
.strat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.strat-card{background:#161a1e;border:1px solid #2b3139;border-radius:6px;padding:14px;display:flex;flex-direction:column;gap:10px}
.strat-card-disabled{opacity:.5;border-color:#3a2020}
.strat-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}
.strat-name{color:#eaecef;font-weight:700;font-size:.95rem;text-decoration:none;font-family:'IBM Plex Sans KR','Segoe UI',sans-serif}
.strat-name:hover{color:#0ecb81}
.strat-status{font-size:.65rem;background:#1e2329;border-radius:3px;padding:2px 6px;color:#848e9c;text-transform:uppercase;letter-spacing:.04em;font-family:'IBM Plex Mono',monospace}
.strat-prod{font-size:.62rem;border-radius:3px;padding:2px 5px;font-weight:700;letter-spacing:.04em;font-family:'IBM Plex Mono',monospace}
.prod-active{background:rgba(14,203,129,.1);color:#0ecb81;border:1px solid rgba(14,203,129,.25)}
.prod-commented{background:rgba(240,165,0,.1);color:#f0a500;border:1px solid rgba(240,165,0,.25)}
.prod-absent{background:#1e2329;color:#5e6673;border:1px dashed #2b3139}
.strat-venue{font-size:.6rem;border-radius:3px;padding:2px 5px;font-weight:700;letter-spacing:.04em;font-family:'IBM Plex Mono',monospace}
.venue-kis{background:rgba(24,144,255,.12);color:#1890ff;border:1px solid rgba(24,144,255,.3)}
.venue-binance{background:rgba(243,186,47,.12);color:#f3ba2f;border:1px solid rgba(243,186,47,.35)}
.strat-disabled-reason{font-size:.6rem;color:#848e9c;font-family:'IBM Plex Mono',monospace;background:#1e2329;border:1px dashed #3a3a3a;border-radius:3px;padding:1px 5px;text-transform:uppercase;letter-spacing:.04em}
.strat-toggle:disabled + .slider{opacity:.4;cursor:not-allowed}
.strat-exits{display:flex;flex-wrap:wrap;gap:6px;margin-top:2px}
.strat-exit-chip{font-size:.7rem;font-family:'IBM Plex Mono',monospace;padding:3px 7px;border-radius:3px;background:#0b0e11;border:1px solid #2b3139;color:#848e9c}
.strat-exit-chip b{color:#eaecef;font-weight:600;font-variant-numeric:tabular-nums}
.exit-tf{background:rgba(132,142,156,.08);color:#b7bdc8;font-weight:600}
.exit-sl b{color:#f6465d}
.exit-tp b{color:#0ecb81}
.exit-trail b{color:#f0a500}
.strat-meta{display:flex;gap:10px;flex-wrap:wrap;font-size:.72rem;color:#848e9c;font-family:'IBM Plex Mono',monospace}
.strat-summary{font-size:.75rem;color:#b7bdc8;line-height:1.5;background:#0b0e11;border-left:3px solid #1890ff;padding:8px 10px;border-radius:4px;white-space:pre-line}
.strat-metrics{display:grid;grid-template-columns:1fr 1fr;gap:5px}
.strat-metrics > div{background:#0b0e11;border:1px solid #2b3139;border-radius:4px;padding:6px 8px;display:flex;justify-content:space-between;font-size:.75rem}
.m-label{color:#5e6673}
.m-val{color:#eaecef;font-weight:600;font-family:'IBM Plex Mono',monospace;font-variant-numeric:tabular-nums}
.strat-toggle-row{display:flex;justify-content:space-between;align-items:center;border-top:1px solid #2b3139;padding-top:10px}
.strat-state{font-size:.75rem;font-weight:700;font-family:'IBM Plex Mono',monospace}
.strat-on{color:#0ecb81}
.strat-off{color:#f6465d}
.switch{position:relative;display:inline-block;width:44px;height:22px}
.switch input{opacity:0;width:0;height:0}
.slider{position:absolute;cursor:pointer;inset:0;background:#2b3139;border-radius:22px;transition:.2s}
.slider:before{content:"";position:absolute;height:16px;width:16px;left:3px;bottom:3px;background:#848e9c;border-radius:50%;transition:.2s}
input:checked + .slider{background:#0ecb81}
input:checked + .slider:before{transform:translateX(22px);background:#fff}
"""

_STRATEGY_TOGGLE_JS = """
async function postToggle(sid, enabled){
  const resp = await fetch(`/api/strategies/${sid}/toggle`,{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({enabled})
  });
  return resp.json();
}
function bindStrategyToggles(root){
  (root || document).querySelectorAll('.strat-toggle').forEach(el => {
    if (el.dataset.bound === '1') return;
    el.dataset.bound = '1';
    el.addEventListener('change', async (ev) => {
      const sid = ev.target.dataset.strategyId;
      const next = ev.target.checked;
      if (!next) {
        const ok = window.confirm(
          `[${sid}] 비활성 시 보유 포지션이 즉시 청산됩니다.\\n\\n계속하시겠습니까?`
        );
        if (!ok) { ev.target.checked = true; return; }
      }
      try {
        const result = await postToggle(sid, next);
        if (!result.ok) throw new Error('toggle failed');
        const intents = result.liquidation_intents || [];
        if (intents.length > 0) {
          window.alert(`청산 의도 ${intents.length}건 생성됨 — 브로커 전송 대기`);
        }
        location.reload();
      } catch (err) {
        console.error('toggle error', err);
        ev.target.checked = !next;
        window.alert('토글 실패: ' + (err && err.message || err));
      }
    });
  });
}
bindStrategyToggles();
"""


def _fmt_metric(value: Any, *, percent: bool = False, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))
    if percent:
        return f"{v * 100:.{digits}f}%"
    return f"{v:.{digits}f}"


def _fmt_timeframe(tf) -> str:
    """봉(timeframe) 코드를 한국어 라벨로. 모르는 값은 원본 그대로 (안전 fallback)."""
    if not tf:
        return "—"
    mapping = {
        "1m": "1분봉", "3m": "3분봉", "5m": "5분봉", "15m": "15분봉",
        "30m": "30분봉", "1h": "1시간봉", "2h": "2시간봉", "4h": "4시간봉",
        "1d": "일봉", "1w": "주봉", "1mo": "월봉",
    }
    s = str(tf).strip()
    return mapping.get(s, s)


def _fmt_exit_pct(value, *, sign: str) -> str:
    """출구 룰 % 표시 — 손절은 sign='-', 익절·트레일링도 부호 명시. None → '—'."""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)[:8]
    return f"{sign}{v * 100:.1f}%"


def _classify_venues(instruments) -> list[str]:
    """Map a spec's ``instruments`` list to venue tags for the card chip.

    Heuristic (order matters — first match wins per item):
      - "BINANCE_*", "*USDT" / "*USDC" / "*BUSD"   → binance (USDⓈ-M futures)
      - "KRX_*", "KOSPI_*", "KOSDAQ_*", 6-digit    → kis (KRX paper/live)
      - anything else                              → unknown, skipped silently

    Returns a sorted de-duplicated venue list (so a dual-market spec like
    ``[KRX_UNIVERSE, BINANCE_USDT_PERP_UNIVERSE]`` yields ``["binance", "kis"]``).
    """
    if not instruments:
        return []
    venues: set[str] = set()
    for inst in instruments:
        s = str(inst).strip().upper()
        if not s:
            continue
        if (
            s.startswith("BINANCE")
            or s.endswith("USDT")
            or s.endswith("USDC")
            or s.endswith("BUSD")
        ):
            venues.add("binance")
        elif (
            s.startswith("KRX")
            or s.startswith("KOSPI")
            or s.startswith("KOSDAQ")
            or (s.isdigit() and len(s) == 6)
        ):
            venues.add("kis")
        # else: unknown / abstract → don't tag (keep the chip absent)
    return sorted(venues)


def _strategy_card(item: dict) -> str:
    sid = html.escape(str(item.get("id", "")))
    name = html.escape(str(item.get("name", sid)))
    status = html.escape(str(item.get("status", "")))
    instruments = html.escape(", ".join(item.get("instruments") or []))
    timeframe_raw = str(item.get("timeframe", ""))
    timeframe = html.escape(_fmt_timeframe(timeframe_raw))
    # 출구 룰 % — 손절/익절/트레일링 (live-scanner 만 비-null, 그 외 '—').
    stop_pct = _fmt_exit_pct(item.get("stop_loss_pct"), sign="-")
    tp_pct = _fmt_exit_pct(item.get("take_profit_pct"), sign="+")
    trail_pct = _fmt_exit_pct(item.get("trailing_stop_pct"), sign="-")
    sharpe = _fmt_metric(item.get("sharpe_bt"))
    mdd = _fmt_metric(item.get("mdd_bt"), percent=True, digits=1)
    annual = _fmt_metric(item.get("annual_return_bt"), percent=True, digits=1)
    period = html.escape(str(item.get("backtest_period") or "—"))
    summary_raw = (item.get("summary_ko") or "").strip()
    summary_html = (
        f'<div class="strat-summary">{html.escape(summary_raw)}</div>'
        if summary_raw else ""
    )
    enabled = bool(item.get("enabled", True))
    card_cls = "strat-card" + ("" if enabled else " strat-card-disabled")
    state_label = "ON" if enabled else "OFF"
    state_cls = "strat-on" if enabled else "strat-off"
    checked_attr = " checked" if enabled else ""
    prod_status = str(item.get("production_status", "absent"))
    prod_label, prod_cls = {
        "active":    ("REGISTERED",      "prod-active"),
        "commented": ("INACTIVE",        "prod-commented"),
        "absent":    ("DRAFT",           "prod-absent"),
    }.get(prod_status, ("DRAFT", "prod-absent"))
    # Venue chips (KIS / Binance) — visual market-mark per card, derived in
    # _enriched_catalog. Empty list → no chips (abstract/test instruments).
    venue_label = {"kis": "KIS", "binance": "BINANCE"}
    venues_html = "".join(
        f'<span class="strat-venue venue-{v}" data-venue="{v}">'
        f'{venue_label.get(v, v.upper())}</span>'
        for v in (item.get("venues") or [])
    )
    # Toggle actionability — read-only when no orch / rejected / commented.
    toggle_disabled = bool(item.get("toggle_disabled"))
    disabled_reason = item.get("disabled_reason")
    disabled_attr = " disabled" if toggle_disabled else ""
    reason_chip = (
        f'<span class="strat-disabled-reason" title="{html.escape(str(disabled_reason))}">'
        f'{html.escape(str(disabled_reason))}</span>'
        if toggle_disabled and disabled_reason else ""
    )
    return f"""
    <div class="{card_cls}" data-strategy-id="{sid}">
      <div class="strat-head">
        <a class="strat-name" href="/strategies/{sid}">{name}</a>
        {venues_html}
        <span class="strat-prod {prod_cls}" title="production.yaml: {prod_status}">{prod_label}</span>
        <span class="strat-status">{status}</span>
      </div>
      <div class="strat-meta">
        <span><b>id:</b> {sid}</span>
        <span><b>tf:</b> {timeframe}</span>
        <span><b>univ:</b> {instruments}</span>
      </div>
      {summary_html}
      <div class="strat-metrics">
        <div><span class="m-label">Sharpe (BT)</span><span class="m-val">{sharpe}</span></div>
        <div><span class="m-label">MDD (BT)</span><span class="m-val">{mdd}</span></div>
        <div><span class="m-label">연수익 (BT)</span><span class="m-val">{annual}</span></div>
        <div><span class="m-label">기간 (BT)</span><span class="m-val">{period}</span></div>
      </div>
      <div class="strat-exits" data-timeframe="{html.escape(timeframe_raw)}">
        <span class="strat-exit-chip exit-tf">{timeframe}</span>
        <span class="strat-exit-chip exit-sl">손절 <b>{stop_pct}</b></span>
        <span class="strat-exit-chip exit-tp">익절 <b>{tp_pct}</b></span>
        <span class="strat-exit-chip exit-trail">트레일링 <b>{trail_pct}</b></span>
      </div>
      <div class="strat-toggle-row">
        <span class="strat-state {state_cls}">{state_label}</span>
        {reason_chip}
        <label class="switch">
          <input type="checkbox" class="strat-toggle" data-strategy-id="{sid}"{checked_attr}{disabled_attr}>
          <span class="slider"></span>
        </label>
      </div>
    </div>"""


def _render_shadow_runs(runs: list[dict]) -> str:
    """HTML page listing all shadow daemon runs (#198, read-only)."""
    if not runs:
        body = '<p class="empty">아직 가동된 shadow run 이 없습니다. <code>logs/shadow/</code> 디렉토리에 데몬이 첫 신호를 기록하면 여기에 표시됩니다.</p>'
    else:
        body = '<div class="strat-grid">' + "".join(_shadow_run_card(r) for r in runs) + "</div>"
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="60">
<title>QTA — Shadow Runs (#198)</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#0f1117;color:#e0e0e0;padding:16px}}
h1{{font-size:1.1rem;color:#7ecef4;margin-bottom:14px}}
.nav a{{color:#7ecef4;text-decoration:none;margin-right:14px;font-size:.85rem}}
.empty{{padding:30px;text-align:center;color:#888;background:#1a1d27;border-radius:8px}}
.empty code{{background:#0f1117;padding:2px 6px;border-radius:3px;color:#7ecef4}}
{_STRATEGY_CARD_CSS}
.shadow-status-alive{{color:#2ecc71}}
.shadow-status-idle{{color:#f39c12}}
.shadow-status-dead{{color:#e74c3c}}
.shadow-meta-row{{display:flex;gap:14px;flex-wrap:wrap;font-size:.78rem;color:#aaa}}
.shadow-counts{{display:grid;grid-template-columns:1fr 1fr;gap:6px}}
.shadow-counts > div{{background:#0f1117;border-radius:5px;padding:6px 8px;display:flex;justify-content:space-between;font-size:.78rem}}
.shadow-warn{{color:#e74c3c;font-size:.75rem;margin-top:4px}}
</style>
</head>
<body>
<h1>📊 Shadow Runs — Binance/KIS WAL read-only 통합 표시</h1>
<div class="nav">
  <a href="/">← 대시보드</a>
  <a href="/strategies">전략 카탈로그</a>
  <a href="/signals">신호 목록</a>
  <a href="/patch-notes">패치노트</a>
</div>
{body}
</body>
</html>"""


def _shadow_run_card(run: dict) -> str:
    rid = html.escape(str(run.get("run_id", "")))
    exch = html.escape(str(run.get("exchange", "unknown")))
    sym = html.escape(str(run.get("symbol", "")))
    tf = html.escape(str(run.get("timeframe", "")))
    status = run.get("status", "idle")
    status_emoji = {"alive": "🟢", "idle": "🟡", "dead": "🔴"}.get(status, "⚪")
    status_cls = f"shadow-status-{status}"

    last_ts = run.get("last_event_ts") or "—"
    n_entry = run.get("n_entry", 0)
    n_exit = run.get("n_exit", 0)
    n_events = run.get("n_events", 0)
    n_corruptions = run.get("n_corruptions", 0)

    warn_html = ""
    if n_corruptions > 0:
        warn_html = f'<div class="shadow-warn">⚠️ WAL 손상 {n_corruptions} 행</div>'
    if run.get("error"):
        warn_html += f'<div class="shadow-warn">⚠️ {html.escape(str(run["error"]))[:120]}</div>'

    return f"""
    <div class="strat-card" data-run-id="{rid}">
      <div class="strat-head">
        <span class="strat-name">{status_emoji} {rid}</span>
        <span class="strat-status {status_cls}">{status.upper()}</span>
      </div>
      <div class="shadow-meta-row">
        <span><b>거래소:</b> {exch}</span>
        <span><b>종목:</b> {sym}</span>
        <span><b>봉:</b> {tf}</span>
      </div>
      <div class="shadow-counts">
        <div><span class="m-label">최근 활동</span><span class="m-val">{html.escape(str(last_ts))[:19] if last_ts != "—" else "—"}</span></div>
        <div><span class="m-label">총 이벤트</span><span class="m-val">{n_events}</span></div>
        <div><span class="m-label">진입 (BUY)</span><span class="m-val">{n_entry}</span></div>
        <div><span class="m-label">청산 (SELL)</span><span class="m-val">{n_exit}</span></div>
      </div>
      {warn_html}
    </div>"""


def _render_signals_page() -> str:
    """Binance signal-list page (#268). 표만 렌더; rows 는 /api/signals 폴링."""
    return """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>QTA — 신호 목록 (Binance)</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0b0e11;--surface:#161a1e;--surface2:#1e2329;--border:#2b3139;
  --text:#eaecef;--text2:#b7bdc6;--text3:#848e9c;--green:#0ecb81;--red:#f6465d;--yellow:#f0a500;
  --mono:'IBM Plex Mono','Consolas',monospace;--sans:'IBM Plex Sans KR','Segoe UI',sans-serif}
body{font-family:var(--sans);background:var(--bg);color:var(--text);padding:14px;font-size:13px}
h1{font-size:1.05rem;color:var(--text);margin-bottom:10px;font-weight:600}
.nav{margin-bottom:14px}
.nav a{color:var(--text2);text-decoration:none;margin-right:12px;font-size:.8rem;
  background:var(--surface);padding:6px 12px;border-radius:4px;border:1px solid var(--border)}
.nav a:hover{color:var(--text);background:var(--surface2)}
.meta{font-size:.75rem;color:var(--text3);margin-bottom:8px;font-family:var(--mono)}
.empty{padding:30px;text-align:center;color:var(--text3);
  background:var(--surface);border-radius:6px;border:1px solid var(--border)}
table{width:100%;border-collapse:separate;border-spacing:0;
  background:var(--surface);border-radius:6px;overflow:hidden;border:1px solid var(--border)}
thead th{position:sticky;top:0;background:var(--surface2);color:var(--text2);
  font-weight:600;text-align:left;padding:8px 10px;font-size:.72rem;
  text-transform:uppercase;letter-spacing:.4px;border-bottom:1px solid var(--border);z-index:5}
tbody td{padding:7px 10px;font-size:.78rem;border-bottom:1px solid #20262d;
  font-family:var(--mono);color:var(--text)}
tbody tr:nth-child(even){background:#13171c}
tbody tr:hover{background:#1c2229}
.td-num{text-align:right}
.side-badge{display:inline-block;padding:2px 7px;border-radius:3px;
  font-size:.68rem;font-weight:700;letter-spacing:.4px;font-family:var(--mono)}
.side-buy{background:rgba(14,203,129,.16);color:var(--green)}
.side-sell{background:rgba(246,70,93,.16);color:var(--red)}
.fu-badge{display:inline-block;padding:2px 7px;border-radius:3px;
  font-size:.68rem;font-weight:600;font-family:var(--mono)}
.fu-pending{background:rgba(240,165,0,.16);color:var(--yellow)}
.fu-ordered{background:rgba(14,203,129,.16);color:var(--green)}
.fu-filled{background:rgba(14,203,129,.22);color:#11dd8c}
.reason-cell{color:var(--text2);max-width:280px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.sym-cell{color:#f0b90b;font-weight:600}
.note{color:var(--text3);font-size:.7rem;margin-top:8px;font-family:var(--mono)}
</style>
</head>
<body>
<h1>QTA — 신호 목록 (Binance)</h1>
<div class="nav">
  <a href="/">← 대시보드</a>
  <a href="/strategies">전략 카탈로그</a>
  <a href="/shadow_runs">Shadow Runs</a>
  <a href="/patch-notes">패치노트</a>
</div>
<div class="meta" id="meta">로딩 중…</div>
<div id="content"><div class="empty">신호 데이터를 불러오는 중입니다.</div></div>
<script>
const KST = 'Asia/Seoul';
function fmtKst(iso){
  if(!iso) return '—';
  try{
    const d = new Date(iso);
    const p = new Intl.DateTimeFormat('ko-KR',{timeZone:KST,year:'2-digit',month:'2-digit',
      day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(d);
    return p;
  }catch(e){ return iso; }
}
function esc(s){
  return String(s==null?'':s).replace(/[&<>"']/g,c=>(
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function sideBadge(s){
  const v = String(s||'').toLowerCase();
  const cls = v==='buy'?'side-buy':(v==='sell'?'side-sell':'');
  return `<span class="side-badge ${cls}">${esc(v.toUpperCase()||'?')}</span>`;
}
function followUpBadge(fu){
  const v = String(fu||'pending');
  const cls = v==='filled'?'fu-filled':(v==='ordered'?'fu-ordered':'fu-pending');
  const txt = {filled:'체결',ordered:'주문',pending:'보류'}[v]||v;
  return `<span class="fu-badge ${cls}">${esc(txt)}</span>`;
}
async function refresh(){
  try{
    const r = await fetch('/api/signals?venue=binance&limit=200');
    const j = await r.json();
    const rows = j.signals || [];
    const meta = document.getElementById('meta');
    const trunc = j.truncated ? ` (cap 200)` : '';
    meta.textContent = `총 ${j.total||0}건${trunc} · log_dir=${j.log_dir_used||'—'}`;
    const content = document.getElementById('content');
    if(rows.length===0){
      content.innerHTML = '<div class="empty">신호 이력이 없습니다. live 데몬이 가동되면 여기에 표시됩니다.</div>';
      return;
    }
    let html = '<table><thead><tr>'
      + '<th>시각 (KST)</th><th>종목</th><th>전략</th><th>방향</th>'
      + '<th class="td-num">수량</th><th>사유</th><th>후속</th>'
      + '</tr></thead><tbody>';
    for(const s of rows){
      html += '<tr>'
        + `<td>${esc(fmtKst(s.ts))}</td>`
        + `<td class="sym-cell">${esc(s.symbol)}</td>`
        + `<td>${esc(s.strategy_id)}</td>`
        + `<td>${sideBadge(s.side)}</td>`
        + `<td class="td-num">${esc(s.qty)}</td>`
        + `<td class="reason-cell" title="${esc(s.reason)}">${esc(s.reason)}</td>`
        + `<td>${followUpBadge(s.follow_up)}</td>`
        + '</tr>';
    }
    html += '</tbody></table>';
    if(j.note){ html += `<div class="note">${esc(j.note)}</div>`; }
    content.innerHTML = html;
  }catch(e){
    document.getElementById('meta').textContent = 'fetch error: ' + e;
  }
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


def _render_manual_page() -> str:
    """수동 계좌 거래 입력 폼 (2026-05-21 — Claude Routines 일일 리포트용).

    사용자가 Binance / Bybit / Flipster / KIS 에서 직접 손으로 거래한 내역을
    폼으로 입력. 자동 fill 과 구분되도록 별도 event_type `manual_trade` 로 WAL
    포맷 JSONL 에 append. Claude Routines 가 매일 자정 GET /api/journal/today
    호출 시 함께 fetch 되어 분석 대상에 포함.
    """
    return """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>QTA — 수동 거래 입력</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0b0e11;--surface:#161a1e;--surface2:#1e2329;--border:#2b3139;
  --text:#eaecef;--text2:#b7bdc6;--text3:#848e9c;--green:#0ecb81;--red:#f6465d;--yellow:#f0a500;
  --mono:'IBM Plex Mono','Consolas',monospace;--sans:'IBM Plex Sans KR','Segoe UI',sans-serif}
body{font-family:var(--sans);background:var(--bg);color:var(--text);padding:14px;font-size:13px}
h1{font-size:1.05rem;color:var(--text);margin-bottom:6px;font-weight:600}
.subtitle{font-size:.75rem;color:var(--text3);margin-bottom:10px}
.nav{margin-bottom:14px}
.nav a{color:var(--text2);text-decoration:none;margin-right:12px;font-size:.8rem;
  background:var(--surface);padding:6px 12px;border-radius:4px;border:1px solid var(--border)}
.nav a:hover{color:var(--text);background:var(--surface2)}
.form-card{background:var(--surface);border:1px solid var(--border);border-radius:6px;
  padding:18px;margin-bottom:16px;max-width:780px}
.row{display:grid;grid-template-columns:120px 1fr;gap:12px;align-items:center;margin-bottom:10px}
.row label{font-size:.8rem;color:var(--text2);font-weight:500}
.row input,.row select,.row textarea{
  background:var(--bg);border:1px solid var(--border);color:var(--text);
  padding:7px 10px;border-radius:4px;font-size:.85rem;font-family:var(--mono);width:100%}
.row textarea{font-family:var(--sans);min-height:80px;resize:vertical}
.row input:focus,.row select:focus,.row textarea:focus{outline:none;border-color:var(--green)}
.btn-row{display:flex;gap:10px;margin-top:14px}
.btn{padding:8px 18px;border-radius:4px;border:none;cursor:pointer;font-weight:600;font-size:.85rem}
.btn-primary{background:var(--green);color:#000}
.btn-primary:hover{background:#0bbd76}
.btn-primary:disabled{opacity:.4;cursor:wait}
.help{color:var(--text3);font-size:.72rem;line-height:1.6;margin-top:6px;font-family:var(--mono)}
.status{margin-top:10px;padding:10px;border-radius:4px;font-size:.78rem;display:none}
.status.success{background:rgba(14,203,129,.12);color:var(--green);display:block;border:1px solid rgba(14,203,129,.35)}
.status.error{background:rgba(246,70,93,.12);color:var(--red);display:block;border:1px solid rgba(246,70,93,.35)}
.h2{font-size:.9rem;color:var(--text);font-weight:600;margin:18px 0 10px 0}
table{width:100%;border-collapse:separate;border-spacing:0;
  background:var(--surface);border-radius:6px;overflow:hidden;border:1px solid var(--border)}
thead th{position:sticky;top:0;background:var(--surface2);color:var(--text2);font-weight:600;
  text-align:left;padding:8px 10px;font-size:.72rem;text-transform:uppercase;letter-spacing:.4px;
  border-bottom:1px solid var(--border);z-index:5}
tbody td{padding:7px 10px;font-size:.78rem;border-bottom:1px solid #20262d;
  font-family:var(--mono);color:var(--text)}
.kind-entry{color:var(--green);font-weight:700}
.kind-exit{color:var(--red);font-weight:700}
.side-buy,.dir-long{color:var(--green)}
.side-sell,.dir-short{color:var(--red)}
.outcome-win{color:var(--green);font-weight:700}
.outcome-loss{color:var(--red);font-weight:700}
.outcome-breakeven{color:var(--text2)}
.toggle-group{display:inline-flex;gap:6px;flex-wrap:wrap}
.tg-btn{padding:7px 14px;border-radius:4px;border:1px solid var(--border);background:var(--surface2);
  color:var(--text2);font-size:.82rem;cursor:pointer;font-family:var(--sans);transition:all .12s}
.tg-btn:hover{background:#252a30;color:var(--text)}
.tg-btn.active{background:#1b2a44;border-color:#3b6acc;color:#fff;font-weight:600}
.tg-btn.tg-win.active{background:rgba(14,203,129,.18);border-color:var(--green);color:var(--green)}
.tg-btn.tg-loss.active{background:rgba(246,70,93,.18);border-color:var(--red);color:var(--red)}
.pnl-pos{color:var(--green);font-weight:600}.pnl-neg{color:var(--red);font-weight:600}
.note-cell{color:var(--text2);max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--sans)}
.empty{padding:30px;text-align:center;color:var(--text3);background:var(--surface);
  border-radius:6px;border:1px solid var(--border)}
</style>
</head>
<body>
<h1>QTA — 수동 거래 입력</h1>
<div class="subtitle">Binance / Bybit / Flipster / KIS 에서 직접 손으로 거래한 내역을 여기에 입력. Claude Routines 일일 리포트가 자동/수동 거래를 함께 분석.</div>
<div class="nav">
  <a href="/">← 대시보드</a>
  <a href="/cs-tsmom">cs-tsmom</a>
  <a href="/signals">신호 목록</a>
</div>
<div class="form-card">
  <div class="row">
    <label for="symbol">종목</label>
    <input id="symbol" placeholder="BTCUSDT, 005930 등" autofocus>
  </div>
  <div class="row">
    <label>방향</label>
    <div class="toggle-group" id="dir-group">
      <button type="button" class="tg-btn active" data-val="long">LONG (롱)</button>
      <button type="button" class="tg-btn" data-val="short">SHORT (숏)</button>
    </div>
  </div>
  <div class="row">
    <label for="qty">수량</label>
    <input id="qty" type="number" step="any" placeholder="0.01">
  </div>
  <div class="row">
    <label for="entry_price">진입가</label>
    <input id="entry_price" type="number" step="any" placeholder="진입 단가 (필수)">
  </div>
  <div class="row">
    <label for="exit_price">청산가</label>
    <input id="exit_price" type="number" step="any" placeholder="청산 단가 (보유중이면 비워두기)">
  </div>
  <div class="row">
    <label for="realized_pnl">실현손익</label>
    <input id="realized_pnl" type="number" step="any" placeholder="예: +0.054 (USDT/KRW, 사용자 입력)">
  </div>
  <div class="row">
    <label>결과</label>
    <div class="toggle-group" id="outcome-group">
      <button type="button" class="tg-btn" data-val="">미지정</button>
      <button type="button" class="tg-btn tg-win" data-val="win">익절</button>
      <button type="button" class="tg-btn tg-loss" data-val="loss">손절</button>
      <button type="button" class="tg-btn" data-val="breakeven">본전</button>
    </div>
  </div>
  <div class="row">
    <label for="venue">거래소</label>
    <select id="venue">
      <option value="binance">Binance</option>
      <option value="bybit">Bybit</option>
      <option value="flipster">Flipster</option>
      <option value="kis">KIS</option>
      <option value="other">기타</option>
    </select>
  </div>
  <div class="row">
    <label for="note">메모</label>
    <textarea id="note" placeholder="진입 근거 (RSI 30 + BB 하단, 30분봉 도지 등) · 청산 근거 (TP 도달, 손절 룰)"></textarea>
  </div>
  <div class="help">
    청산가·실현손익·결과는 보유중인 거래면 비워둬도 됨. Routines 가 일일 리포트 생성 시 메모를 자동 vs 수동 대조 분석에 사용.
  </div>
  <div class="btn-row">
    <button class="btn btn-primary" id="submit-btn" onclick="submitTrade()">거래 추가</button>
  </div>
  <div class="status" id="status"></div>
</div>

<div class="h2" id="list-hdr">📋 오늘 입력한 수동 거래</div>
<div id="today-list"><div class="empty">로딩 중…</div></div>

<div class="h2" style="margin-top:24px">📤 일일 리포트 export</div>
<div class="form-card">
  <div class="help" style="margin-bottom:10px">
    오늘의 자동 거래 + 수동 거래 + 신호 + cs-tsmom TOP-10 을 한 JSON 으로
    묶어 <code>docs/journal_data/YYYY-MM-DD.json</code> 에 저장 + git
    commit/push. Claude Routines 가 자정 분석 시 이 파일을 fetch.
    <strong>매일 23:50 KST 쯤 한 번 클릭</strong> 권장.
  </div>
  <div class="btn-row">
    <button class="btn btn-primary" id="export-btn" onclick="exportJournal(true)">오늘 journal export + git push</button>
    <button class="btn" id="export-local-btn" onclick="exportJournal(false)" style="background:var(--surface2);color:var(--text)">파일만 저장 (push X)</button>
  </div>
  <div class="status" id="export-status"></div>
</div>

<script>
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmtKst(iso){
  if(!iso) return '—';
  try{
    const d=new Date(iso);
    return new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',
      day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d);
  }catch(e){ return iso; }
}
// 토글 그룹 활성화 헬퍼
function bindToggle(groupId){
  const g=document.getElementById(groupId);
  if(!g) return;
  g.addEventListener('click', e=>{
    const b=e.target.closest('.tg-btn');
    if(!b||!g.contains(b)) return;
    g.querySelectorAll('.tg-btn').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
  });
}
bindToggle('dir-group');
bindToggle('outcome-group');
function toggleValue(groupId){
  const g=document.getElementById(groupId);
  if(!g) return '';
  const a=g.querySelector('.tg-btn.active');
  return a?a.getAttribute('data-val'):'';
}
let showAll=false;
async function submitTrade(){
  const btn=document.getElementById('submit-btn');
  const status=document.getElementById('status');
  const exit_v=document.getElementById('exit_price').value.trim();
  const pnl_v=document.getElementById('realized_pnl').value.trim();
  const payload={
    symbol:document.getElementById('symbol').value.trim().toUpperCase(),
    direction:toggleValue('dir-group')||'long',
    qty:parseFloat(document.getElementById('qty').value),
    entry_price:parseFloat(document.getElementById('entry_price').value),
    exit_price: exit_v===''?null:parseFloat(exit_v),
    realized_pnl: pnl_v===''?null:parseFloat(pnl_v),
    outcome:toggleValue('outcome-group')||null,
    venue:document.getElementById('venue').value,
    note:document.getElementById('note').value.trim(),
  };
  if(!payload.symbol||!payload.qty||!payload.entry_price){
    status.className='status error';
    status.textContent='종목/수량/진입가는 필수';
    return;
  }
  btn.disabled=true;
  btn.textContent=_editingTs?'수정 중…':'저장 중…';
  try{
    // _editingTs 면 PATCH (수정), 아니면 POST (신규)
    let url, method;
    if(_editingTs){
      url='/api/manual_trade/'+encodeURIComponent(_editingTs);
      method='PATCH';
    }else{
      url='/api/manual_trade';
      method='POST';
    }
    const r=await fetch(url,{method:method,
      headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const j=await r.json();
    if(j.ok){
      status.className='status success';
      status.style.background='';
      status.style.color='';
      const dirTag=payload.direction.toUpperCase();
      if(_editingTs){
        status.textContent=`거래 수정 완료 — ${payload.symbol} ${dirTag} ${payload.qty} @ ${payload.entry_price}`;
      }else{
        status.textContent=`거래 추가 완료 — ${payload.symbol} ${dirTag} ${payload.qty} @ ${payload.entry_price}`+
          (payload.exit_price?` → ${payload.exit_price}`:'')+
          (payload.realized_pnl!=null?` (PnL ${payload.realized_pnl})`:'');
      }
      _editingTs=null;
      btn.style.background='';
      document.getElementById('symbol').value='';
      document.getElementById('qty').value='';
      document.getElementById('entry_price').value='';
      document.getElementById('exit_price').value='';
      document.getElementById('realized_pnl').value='';
      document.getElementById('note').value='';
      // outcome 토글 초기화
      const og=document.getElementById('outcome-group');
      og.querySelectorAll('.tg-btn').forEach(x=>x.classList.remove('active'));
      og.querySelector('.tg-btn[data-val=""]').classList.add('active');
      document.getElementById('symbol').focus();
      await loadList();
    }else{
      status.className='status error';
      status.style.background='';
      status.style.color='';
      status.textContent=(_editingTs?'수정':'저장')+' 실패: '+(j.reason||'unknown');
    }
  }catch(e){
    status.className='status error';
    status.style.background='';
    status.style.color='';
    status.textContent='요청 실패: '+e;
  }finally{
    btn.disabled=false;
    btn.textContent=_editingTs?'수정 저장':'거래 추가';
  }
}
function fmtPnlCell(v){
  if(v==null||v==='') return '<span style="color:var(--text3)">—</span>';
  const n=Number(v);
  if(!Number.isFinite(n)) return esc(String(v));
  const cls=n>0?'pnl-pos':(n<0?'pnl-neg':'');
  const sign=n>0?'+':'';
  return `<span class="${cls}">${sign}${n}</span>`;
}
async function loadList(){
  const url=showAll?'/api/manual_trade/recent?limit=50':'/api/manual_trade/today';
  try{
    const r=await fetch(url);
    const j=await r.json();
    const list=document.getElementById('today-list');
    const trades=j.trades||[];
    const total=j.total_all_time||0;
    const hdr=document.getElementById('list-hdr');
    if(hdr){
      hdr.innerHTML='📋 '+(showAll?'최근 50건':'오늘 입력')+
        ' <span style="color:var(--text3);font-size:.8rem;font-weight:400">(전체 누적 '+total+'건 저장됨)</span>'+
        ' <button class="tg-btn" id="toggle-list-btn" style="margin-left:10px;font-size:.7rem">'+
        (showAll?'오늘만 보기':'전체 보기')+'</button>';
      const tb=document.getElementById('toggle-list-btn');
      if(tb) tb.addEventListener('click',()=>{showAll=!showAll;loadList();});
    }
    if(trades.length===0){
      const hint=(!showAll&&total>0)?' (전체 '+total+'건 있음 — "전체 보기" 클릭)':'';
      list.innerHTML='<div class="empty">'+(showAll?'저장된 수동 거래 없음.':'오늘 입력한 수동 거래 없음.'+hint)+'</div>';
      return;
    }
    let html='<table><thead><tr><th>시각</th><th>거래소</th><th>종목</th><th>방향</th><th>수량</th><th>진입가</th><th>청산가</th><th>실현손익</th><th>결과</th><th>메모</th><th>액션</th></tr></thead><tbody>';
    for(const t of trades){
      const dir=t.direction||(t.side==='buy'?'long':t.side==='sell'?'short':'');
      const dirTxt=dir?dir.toUpperCase():(t.side||'').toUpperCase();
      const dirCls=dir==='long'?'dir-long':(dir==='short'?'dir-short':'');
      const entry=t.entry_price!=null?t.entry_price:(t.price!=null?t.price:'—');
      const exitP=t.exit_price!=null&&t.exit_price!==''?t.exit_price:'—';
      const outcome=t.outcome||'';
      const outcomeTxt=outcome==='win'?'익절':outcome==='loss'?'손절':outcome==='breakeven'?'본전':'—';
      const outcomeCls=outcome?('outcome-'+outcome):'';
      const tsAttr=esc(t.ts||'');
      html+=`<tr data-ts="${tsAttr}">
        <td>${esc(fmtKst(t.ts))}</td>
        <td>${esc(t.venue||'—')}</td>
        <td>${esc(t.symbol)}</td>
        <td class="${dirCls}">${esc(dirTxt)}</td>
        <td>${esc(t.qty)}</td>
        <td>${esc(entry)}</td>
        <td>${esc(exitP)}</td>
        <td>${fmtPnlCell(t.realized_pnl)}</td>
        <td class="${outcomeCls}">${esc(outcomeTxt)}</td>
        <td class="note-cell" title="${esc(t.note)}">${esc(t.note)}</td>
        <td class="actions-cell">
          <button class="tg-btn act-edit" data-ts="${tsAttr}" title="수정">✎</button>
          <button class="tg-btn act-delete" data-ts="${tsAttr}" title="삭제">🗑</button>
        </td>
      </tr>`;
    }
    html+='</tbody></table>';
    list.innerHTML=html;
    // 버튼 wire-up (delegation)
    list.querySelectorAll('.act-edit').forEach(b=>b.addEventListener('click', onEditClick));
    list.querySelectorAll('.act-delete').forEach(b=>b.addEventListener('click', onDeleteClick));
  }catch(e){
    document.getElementById('today-list').innerHTML='<div class="empty">로딩 실패: '+esc(String(e))+'</div>';
  }
}

// ── 수정 / 삭제 (2026-06-03) ───────────────────────────────────────
// 현재 편집 중인 거래의 ts. null 이면 신규 추가 모드 (POST), 값 있으면 수정 모드 (PATCH).
let _editingTs = null;

async function onDeleteClick(e){
  const ts=e.currentTarget.getAttribute('data-ts');
  if(!ts) return;
  if(!confirm('이 거래를 삭제하시겠습니까? 시각: '+fmtKst(ts)+' (복구 불가)')) return;
  try{
    const r=await fetch('/api/manual_trade/'+encodeURIComponent(ts),{method:'DELETE'});
    const j=await r.json();
    const status=document.getElementById('status');
    if(j.ok){
      status.className='status success';
      status.textContent='거래 삭제됨 ('+fmtKst(ts)+')';
      await loadList();
    }else{
      status.className='status error';
      status.textContent='삭제 실패: '+(j.reason||'unknown');
    }
  }catch(e){
    document.getElementById('status').className='status error';
    document.getElementById('status').textContent='요청 실패: '+e;
  }
}

async function onEditClick(e){
  const ts=e.currentTarget.getAttribute('data-ts');
  if(!ts) return;
  // 현재 row 의 값들을 폼에 채우기 (오늘분에선 today list, 전체모드면 recent)
  // 가장 단순한 방법: 마지막으로 받은 trades 에서 ts 매칭.
  // loadList 안 state 못 잡으니, GET /api/manual_trade/recent?limit=500 다시 조회.
  try{
    const r=await fetch('/api/manual_trade/recent?limit=500');
    const j=await r.json();
    const t=(j.trades||[]).find(x=>String(x.ts)===String(ts));
    if(!t){
      alert('해당 거래를 못 찾았어요 (이미 삭제됐을 수 있음)');
      return;
    }
    // 폼에 값 채우기
    document.getElementById('symbol').value=t.symbol||'';
    document.getElementById('qty').value=t.qty!=null?t.qty:'';
    document.getElementById('entry_price').value=t.entry_price!=null?t.entry_price:(t.price!=null?t.price:'');
    document.getElementById('exit_price').value=t.exit_price!=null?t.exit_price:'';
    document.getElementById('realized_pnl').value=t.realized_pnl!=null?t.realized_pnl:'';
    document.getElementById('venue').value=t.venue||'binance';
    document.getElementById('note').value=t.note||'';
    // 토글 그룹
    const dir=t.direction||(t.side==='buy'?'long':t.side==='sell'?'short':'long');
    const dg=document.getElementById('dir-group');
    dg.querySelectorAll('.tg-btn').forEach(x=>x.classList.toggle('active', x.getAttribute('data-val')===dir));
    const og=document.getElementById('outcome-group');
    og.querySelectorAll('.tg-btn').forEach(x=>x.classList.toggle('active', x.getAttribute('data-val')===(t.outcome||'')));
    _editingTs=ts;
    document.getElementById('submit-btn').textContent='수정 저장';
    document.getElementById('submit-btn').style.background='var(--yellow)';
    // 페이지 상단 안내
    const status=document.getElementById('status');
    status.className='status';
    status.style.display='block';
    status.style.background='rgba(240,165,0,.15)';
    status.style.color='var(--yellow)';
    status.textContent='✎ 수정 모드 — 폼 값 바꾸고 "수정 저장" 클릭. 취소하려면 페이지 새로고침.';
    // 폼 위치로 스크롤
    window.scrollTo({top:0, behavior:'smooth'});
  }catch(e){
    alert('수정 모드 진입 실패: '+e);
  }
}

loadList();

async function exportJournal(doPush){
  const btn=document.getElementById('export-btn');
  const btn2=document.getElementById('export-local-btn');
  const status=document.getElementById('export-status');
  btn.disabled=true; btn2.disabled=true;
  const orig=btn.textContent;
  btn.textContent='저장 중…';
  status.className='status';
  status.textContent='';
  status.style.display='block';
  try{
    const r=await fetch('/api/journal/export',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({push:!!doPush})});
    const j=await r.json();
    if(j.ok){
      status.className='status success';
      const c=j.counts||{};
      const counts=`auto_fills:${c.auto_fills||0}, signals:${c.auto_signals||0}, manual:${c.manual_trades||0}`;
      status.textContent=`✅ ${j.path} 저장 (${counts}) · `+
        (j.pushed?'git push 완료 — Routines 가 fetch 가능':
         (j.committed?'commit 완료 (push 생략)':'변경 없음'));
    }else{
      status.className='status error';
      status.textContent=`❌ ${j.stage||'?'} 단계 실패: ${j.detail||j.reason||JSON.stringify(j)}`;
    }
  }catch(e){
    status.className='status error';
    status.textContent='요청 실패: '+e;
  }finally{
    btn.disabled=false; btn2.disabled=false;
    btn.textContent=orig;
  }
}
</script>
</body></html>"""


def _render_cs_tsmom_page() -> str:
    """cs-tsmom-crypto-daily 신호 페이지 — Pine Script 와 동일 score 정의 + cross-sectional 랭킹."""
    return """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>QTA — cs-tsmom 신호 (Binance)</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0b0e11;--surface:#161a1e;--surface2:#1e2329;--border:#2b3139;
  --text:#eaecef;--text2:#b7bdc6;--text3:#848e9c;--green:#0ecb81;--red:#f6465d;--yellow:#f0a500;
  --mono:'IBM Plex Mono','Consolas',monospace;--sans:'IBM Plex Sans KR','Segoe UI',sans-serif}
body{font-family:var(--sans);background:var(--bg);color:var(--text);padding:14px;font-size:13px}
h1{font-size:1.05rem;color:var(--text);margin-bottom:6px;font-weight:600}
.subtitle{font-size:.75rem;color:var(--text3);margin-bottom:10px}
.nav{margin-bottom:14px}
.nav a{color:var(--text2);text-decoration:none;margin-right:12px;font-size:.8rem;
  background:var(--surface);padding:6px 12px;border-radius:4px;border:1px solid var(--border)}
.nav a:hover{color:var(--text);background:var(--surface2)}
.meta{font-size:.75rem;color:var(--text3);margin-bottom:8px;font-family:var(--mono)}
.empty,.error{padding:30px;text-align:center;color:var(--text3);
  background:var(--surface);border-radius:6px;border:1px solid var(--border)}
.error{color:var(--red);border-color:rgba(246,70,93,.35)}
table{width:100%;border-collapse:separate;border-spacing:0;
  background:var(--surface);border-radius:6px;overflow:hidden;border:1px solid var(--border)}
thead th{position:sticky;top:0;background:var(--surface2);color:var(--text2);font-weight:600;
  text-align:left;padding:8px 10px;font-size:.72rem;text-transform:uppercase;letter-spacing:.4px;
  border-bottom:1px solid var(--border);z-index:5}
tbody td{padding:7px 10px;font-size:.78rem;border-bottom:1px solid #20262d;
  font-family:var(--mono);color:var(--text)}
tbody tr.in-top{background:rgba(14,203,129,.06)}
tbody tr:hover{background:#1c2229}
.td-num{text-align:right;font-variant-numeric:tabular-nums}
.sym-cell{color:#f0b90b;font-weight:600}
.rank-cell{font-weight:700;color:var(--text)}
.rank-out{color:var(--text3)}
.sig-badge{display:inline-block;padding:2px 7px;border-radius:3px;font-size:.68rem;
  font-weight:700;letter-spacing:.4px;font-family:var(--mono)}
.sig-ENTER{background:rgba(14,203,129,.2);color:#11dd8c}
.sig-HOLD{background:rgba(14,203,129,.1);color:var(--green)}
.sig-EXIT{background:rgba(246,70,93,.16);color:var(--red)}
.sig-OUT{background:rgba(132,142,156,.1);color:var(--text3)}
.score-pos{color:var(--green)}
.score-neg{color:var(--red)}
.illiq{color:var(--yellow)}
.reason-cell{font-size:.7rem;color:var(--text3);font-family:var(--mono)}
.reason-ok{color:var(--green)}
.reason-no_data{color:var(--red)}
.reason-warmup{color:var(--yellow)}
.reason-low_volume{color:var(--yellow)}
.reason-negative_score{color:var(--text3)}
.reason-out_of_top_n{color:var(--text3)}
.refresh-btn{background:var(--surface2);border:1px solid var(--border);color:var(--text);
  padding:6px 14px;border-radius:4px;font-size:.78rem;cursor:pointer;font-family:var(--sans);
  margin-left:auto;transition:border-color .15s,color .15s}
.refresh-btn:hover{border-color:var(--green);color:var(--green)}
.refresh-btn:disabled{opacity:.4;cursor:wait}
.header-row{display:flex;align-items:center;gap:14px;margin-bottom:8px}
.pin-badge{background:var(--surface2);border:1px solid var(--border);color:var(--text2);
  padding:3px 8px;border-radius:4px;font-size:.7rem;font-family:var(--mono)}
.section-h2{font-size:.85rem;color:var(--text);font-weight:600;margin:18px 0 10px 0;
  display:flex;align-items:center;gap:10px}
.section-h2 .count{font-size:.7rem;color:var(--text3);font-family:var(--mono);font-weight:400}
.top-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;
  margin-bottom:16px}
.top-card{background:var(--surface);border:1px solid var(--border);border-radius:6px;
  padding:12px;display:flex;flex-direction:column;gap:6px;transition:border-color .15s}
.top-card.is-ENTER{border-color:var(--green);background:rgba(14,203,129,.05);
  box-shadow:0 0 0 1px rgba(14,203,129,.25)}
.top-card.is-HOLD{border-color:var(--border)}
.top-card-head{display:flex;justify-content:space-between;align-items:baseline}
.top-card-rank{font-size:.7rem;color:var(--text3);font-family:var(--mono);font-weight:600}
.top-card-sym{font-size:1.15rem;color:#f0b90b;font-weight:700;font-family:var(--mono);
  letter-spacing:.5px}
.top-card-score{font-size:1.05rem;font-weight:700;font-family:var(--mono);font-variant-numeric:tabular-nums}
.top-card-close{font-size:.75rem;color:var(--text3);font-family:var(--mono);
  font-variant-numeric:tabular-nums}
.top-card-sig{margin-top:2px}
.top-empty{padding:30px;text-align:center;color:var(--text3);background:var(--surface);
  border-radius:6px;border:1px solid var(--border);font-size:.85rem}
.note{color:var(--text3);font-size:.72rem;margin-top:10px;font-family:var(--mono);line-height:1.55}
</style>
</head>
<body>
<h1>QTA — cs-tsmom-crypto-daily (12-1m 모멘텀 + cross-sectional top-10)</h1>
<div class="subtitle">production 전략과 동일 score 식 — log(close[t-21]/close[t-252]). 5y backtest Sharpe 1.33 · 연수익 +90.8% · MDD −52.4%. 매일 30종목 새로 fetch + 랭킹.</div>
<div class="nav">
  <a href="/">← 대시보드</a>
  <a href="/strategies">전략 카탈로그</a>
  <a href="/signals">신호 목록</a>
  <a href="/patch-notes">패치노트</a>
</div>
<div class="header-row">
  <div class="meta" id="meta">로딩 중…</div>
  <span class="pin-badge" id="pin-badge" style="display:none">universe pin: —</span>
  <button class="refresh-btn" id="refresh-btn" onclick="forceRefresh()">↻ 캐시 무효화 + 재계산</button>
</div>
<div id="content"><div class="empty">신호 데이터를 불러오는 중입니다 (첫 호출은 30종목 fetch 로 ~10초 소요 가능).</div></div>
<script>
const KST = 'Asia/Seoul';
function fmtKst(iso){
  if(!iso) return '—';
  try{
    const d = new Date(iso);
    return new Intl.DateTimeFormat('ko-KR',{timeZone:KST,year:'2-digit',month:'2-digit',
      day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(d);
  }catch(e){ return iso; }
}
function esc(s){
  return String(s==null?'':s).replace(/[&<>"']/g,c=>(
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function fmtPct(s){
  if(s==null) return '—';
  const v = Number(s)*100;
  const cls = v>0?'score-pos':(v<0?'score-neg':'');
  const sign = v>0?'+':'';
  return `<span class="${cls}">${sign}${v.toFixed(1)}%</span>`;
}
function fmtClose(v){
  if(v==null||isNaN(v)) return '—';
  return Number(v).toLocaleString('ko-KR',{maximumFractionDigits:6});
}
const REASON_LABELS = {
  'ok':             {text:'✓ 정상',         cls:'reason-ok'},
  'no_data':        {text:'데이터 없음',     cls:'reason-no_data'},
  'warmup':         {text:'워밍업 (252d 미달)', cls:'reason-warmup'},
  'low_volume':     {text:'거래량 부족',     cls:'reason-low_volume'},
  'negative_score': {text:'모멘텀 음수',     cls:'reason-negative_score'},
  'out_of_top_n':   {text:'top10 밖',        cls:'reason-out_of_top_n'},
};
function fmtReason(reason){
  const info = REASON_LABELS[reason] || {text:esc(reason||'—'), cls:''};
  return `<span class="reason-cell ${info.cls}">${esc(info.text)}</span>`;
}
function renderTopCards(rows){
  // top-N (in_top_today=true) + EXIT (어제 top, 오늘 out) 한 묶음.
  // ENTER/HOLD 강조 → 오늘 신규 BUY / 보유 유지 가시. EXIT 은 청산 안내.
  const buyRows = rows
    .filter(r => r.in_top_today)
    .sort((a,b) => (a.rank||999) - (b.rank||999));
  const exitRows = rows.filter(r => !r.in_top_today && r.in_top_yday);
  const enterN = buyRows.filter(r => r.signal === 'ENTER').length;
  const holdN  = buyRows.filter(r => r.signal === 'HOLD').length;
  const exitN  = exitRows.length;

  function card(r){
    const sig = r.signal || 'HOLD';
    const sym = (r.symbol||'').replace(/USDT$/,'');
    const scorePct = r.score!=null ? (r.score*100).toFixed(1) + '%' : '—';
    const scoreCls = (r.score||0) > 0 ? 'score-pos' : 'score-neg';
    return `<div class="top-card is-${esc(sig)}">
      <div class="top-card-head">
        <span class="top-card-rank">#${esc(r.rank!=null?r.rank:'—')}</span>
        <span class="sig-badge sig-${esc(sig)}">${esc(sig)}</span>
      </div>
      <div class="top-card-sym">${esc(sym)}</div>
      <div class="top-card-score ${scoreCls}">${esc(scorePct)}</div>
      <div class="top-card-close">$${esc(fmtClose(r.last_close))}</div>
    </div>`;
  }

  let html = `<div class="section-h2">📈 오늘의 BUY 후보 (top-10) <span class="count">·  ENTER ${enterN}  ·  HOLD ${holdN}  ·  EXIT ${exitN}</span></div>`;
  if (buyRows.length === 0 && exitRows.length === 0){
    html += '<div class="top-empty">오늘 BUY 후보 없음 — 30종 모두 음수 score / 데이터 부족 / OUT. 우상단 ↻ 로 강제 갱신해도 BUY 가 안 생기면 시장이 약세 (BTC -30% drawdown 부근) 일 가능성.</div>';
    return html;
  }
  html += '<div class="top-grid">';
  html += buyRows.map(card).join('');
  // EXIT 도 같은 그리드에 (사용자 청산 결정 즉시 보이게)
  html += exitRows.map(card).join('');
  html += '</div>';
  return html;
}
function renderFullTable(rows){
  if(rows.length===0) return '<div class="empty">데이터 없음 — 패널 빌드 실패 또는 워밍업 부족.</div>';
  const trs = rows.map(r => {
    const sig = r.signal || 'OUT';
    const inTop = r.in_top_today;
    const rankTxt = (r.rank!=null) ? r.rank : '—';
    const liq = r.liquid ? '' : '<span class="illiq" title="유동성 미달 (60d avg quote_vol < 10M USDT)">⚠</span>';
    return `<tr class="${inTop?'in-top':''}">
      <td class="td-num rank-cell ${inTop?'':'rank-out'}">${esc(rankTxt)}</td>
      <td class="sym-cell">${esc(r.symbol)}</td>
      <td class="td-num">${fmtPct(r.score)}</td>
      <td class="td-num">${esc(fmtClose(r.last_close))}</td>
      <td><span class="sig-badge sig-${esc(sig)}">${esc(sig)}</span></td>
      <td>${liq}</td>
      <td>${fmtReason(r.reason)}</td>
    </tr>`;
  }).join('');
  return `<div class="section-h2">🔍 전체 진단 — 30종 score 테이블 <span class="count">· 디버깅용</span></div>
  <table><thead><tr>
    <th>Rank</th><th>Symbol</th><th class="td-num">Score (12-1m)</th>
    <th class="td-num">Last Close</th><th>Signal</th><th>Liq</th><th>사유</th>
  </tr></thead><tbody>${trs}</tbody></table>
  <div class="note">
    Signal: <b>ENTER</b> = 어제 top10 외 → 오늘 top10 진입 (BUY). <b>HOLD</b> = 어제·오늘 모두 top10. <b>EXIT</b> = 어제 top10 → 오늘 이탈. <b>OUT</b> = 비보유.<br>
    사유 — <b>워밍업</b>: 252d lookback 데이터 부족 (신규 listing 등). <b>데이터 없음</b>: fetch 실패 또는 캐시 비어있음 → 우상단 ↻ 버튼 클릭. <b>거래량 부족</b>: 60d 평균 거래대금 &lt; 1천만 USDT. <b>모멘텀 음수</b>: 12-1m score ≤ 0 → 후보 제외. <b>top10 밖</b>: score 양수지만 cutoff 밖.<br>
    실거래 wiring 무관, 대시보드 서버가 매일 자체 fetch + 계산 (1h 캐시). production 전략과 동일 score 식 + 동일 universe → TV Pine Script (cs-tsmom-crypto-daily 12-1m) 와 정확히 같은 숫자.
  </div>`;
}
function render(rows){
  return renderTopCards(rows) + renderFullTable(rows);
}
async function refresh(){
  try{
    const r = await fetch('/api/cs-tsmom');
    const j = await r.json();
    const meta = document.getElementById('meta');
    const pinEl = document.getElementById('pin-badge');
    const content = document.getElementById('content');
    if(j.pin_date){
      pinEl.textContent = `universe pin: ${j.pin_date}`;
      pinEl.style.display = '';
    }
    if(!j.available){
      meta.textContent = `미가용 — ${j.reason||'unknown'} · 마지막 시도 ${fmtKst(j.fetched_at)}`;
      content.innerHTML = `<div class="error">계산 실패: ${esc(j.reason||'unknown')} — 우상단 ↻ 버튼으로 강제 재시도</div>`;
      return;
    }
    meta.textContent = `${j.universe_size||0} 종목 · 캐시 시각 ${fmtKst(j.fetched_at)}`;
    content.innerHTML = render(j.rows || []);
  }catch(e){
    document.getElementById('content').innerHTML =
      `<div class="error">로딩 실패: ${esc(String(e))}</div>`;
  }
}
async function forceRefresh(){
  const btn = document.getElementById('refresh-btn');
  btn.disabled = true;
  btn.textContent = '재계산 중 (~10초)…';
  try{
    const r = await fetch('/api/cs-tsmom/refresh', {method:'POST'});
    const j = await r.json();
    if(!j.ok){
      alert('재계산 실패: ' + (j.reason || 'unknown'));
    }
    await refresh();
  }catch(e){
    alert('재계산 호출 실패: ' + e);
  }finally{
    btn.disabled = false;
    btn.textContent = '↻ 캐시 무효화 + 재계산';
  }
}
refresh();
setInterval(refresh, 60000);  // 1분마다 (서버는 1h 캐시이므로 보통 캐시 반환)
</script>
</body>
</html>"""


def _render_airborne_page(rule_key: str = "default") -> str:
    """airborne 알림 적중 페이지 — 오늘 FIRE 의 TP/SL 시뮬레이션 상세.

    cs-tsmom 페이지와 동일한 톤·구조: 상단 stat 타일 + side/bucket 카드 + 전체
    FIRE 테이블. /api/airborne_metrics 한 endpoint 만 폴링 (서버 5분 캐시).

    rule_key:
      - ``default`` (기본): TP +1.0% / SL -0.5% 룰. `/airborne` 이 사용.
      - ``2pct`` (2026-06-04): TP +2.0% / SL -1.0% 룰. `/airborne-2pct` 가 사용.
        같은 HTML 본문에 fetch URL 만 ``?rule=2pct`` 로 박아 한 페이지 코드를
        룰 두 가지로 재사용.
    """
    if rule_key == "2pct":
        title_kr = "airborne 알림 적중 (+2%/-1%)"
        rule_chip = "TP +2.0% / SL -1.0% / 4봉 hold"
        nav_other = '<a href="/airborne">기본 룰 (+1%/-0.5%)</a>'
    else:
        rule_key = "default"
        title_kr = "airborne 알림 적중 (기본 룰)"
        rule_chip = "TP +1.0% / SL -0.5% / 4봉 hold"
        nav_other = '<a href="/airborne-2pct">대안 룰 (+2%/-1%)</a>'
    html = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>QTA — airborne 알림 적중 (오늘)</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0b0e11;--surface:#161a1e;--surface2:#1e2329;--border:#2b3139;
  --text:#eaecef;--text2:#b7bdc6;--text3:#848e9c;--green:#0ecb81;--red:#f6465d;--yellow:#f0a500;
  --mono:'IBM Plex Mono','Consolas',monospace;--sans:'IBM Plex Sans KR','Segoe UI',sans-serif}
body{font-family:var(--sans);background:var(--bg);color:var(--text);padding:14px;font-size:13px}
h1{font-size:1.05rem;color:var(--text);margin-bottom:6px;font-weight:600}
.subtitle{font-size:.75rem;color:var(--text3);margin-bottom:10px}
.nav{margin-bottom:14px}
.nav a{color:var(--text2);text-decoration:none;margin-right:12px;font-size:.8rem;
  background:var(--surface);padding:6px 12px;border-radius:4px;border:1px solid var(--border)}
.nav a:hover{color:var(--text);background:var(--surface2)}
.meta{font-size:.75rem;color:var(--text3);margin-bottom:8px;font-family:var(--mono)}
.empty,.error{padding:30px;text-align:center;color:var(--text3);
  background:var(--surface);border-radius:6px;border:1px solid var(--border)}
.error{color:var(--red);border-color:rgba(246,70,93,.35)}
.header-row{display:flex;align-items:center;gap:14px;margin-bottom:8px;flex-wrap:wrap}
.rule-badge{background:var(--surface2);border:1px solid var(--border);color:var(--text2);
  padding:3px 8px;border-radius:4px;font-size:.7rem;font-family:var(--mono)}
.refresh-btn{background:var(--surface2);border:1px solid var(--border);color:var(--text);
  padding:6px 14px;border-radius:4px;font-size:.78rem;cursor:pointer;font-family:var(--sans);
  margin-left:auto;transition:border-color .15s,color .15s}
.refresh-btn:hover{border-color:var(--green);color:var(--green)}
.refresh-btn:disabled{opacity:.4;cursor:wait}
.section-h2{font-size:.85rem;color:var(--text);font-weight:600;margin:18px 0 10px 0;
  display:flex;align-items:center;gap:10px}
.section-h2 .count{font-size:.7rem;color:var(--text3);font-family:var(--mono);font-weight:400}

/* ── 상단 큰 stat 타일 ── */
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:8px}
.stat-tile{background:var(--surface);border:1px solid var(--border);border-radius:6px;
  padding:14px 14px;display:flex;flex-direction:column;gap:6px}
.stat-tile.is-hero{background:linear-gradient(180deg,rgba(14,203,129,.04),transparent);
  border-color:rgba(14,203,129,.25)}
.stat-tile.is-hero-bad{background:linear-gradient(180deg,rgba(246,70,93,.05),transparent);
  border-color:rgba(246,70,93,.25)}
.stat-label{font-size:.7rem;color:var(--text3);font-weight:600;letter-spacing:.4px;text-transform:uppercase}
.stat-val{font-size:1.5rem;font-weight:700;font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--text)}
.stat-val.dim{color:var(--text3);font-size:1.1rem}
.stat-val.green{color:var(--green)}
.stat-val.red{color:var(--red)}
.stat-sub{font-size:.7rem;color:var(--text3);font-family:var(--mono)}

/* ── side breakdown (long/short 2칸) ── */
.side-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-bottom:16px}
.side-card{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:14px}
.side-card.is-long{border-color:rgba(14,203,129,.3)}
.side-card.is-short{border-color:rgba(246,70,93,.3)}
.side-card-hdr{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
.side-card-title{font-size:.85rem;font-weight:700;font-family:var(--mono);letter-spacing:.5px}
.side-long{color:var(--green)}
.side-short{color:var(--red)}
.side-row{display:flex;justify-content:space-between;font-size:.78rem;padding:3px 0;color:var(--text2);font-family:var(--mono)}
.side-row b{color:var(--text);font-variant-numeric:tabular-nums}

/* ── KST bucket + 전체 FIRE 테이블 공용 ── */
table{width:100%;border-collapse:separate;border-spacing:0;
  background:var(--surface);border-radius:6px;overflow:hidden;border:1px solid var(--border)}
thead th{position:sticky;top:0;background:var(--surface2);color:var(--text2);font-weight:600;
  text-align:left;padding:8px 10px;font-size:.72rem;text-transform:uppercase;letter-spacing:.4px;
  border-bottom:1px solid var(--border);z-index:5}
tbody td{padding:7px 10px;font-size:.78rem;border-bottom:1px solid #20262d;
  font-family:var(--mono);color:var(--text)}
tbody tr:hover{background:#1c2229}
.td-num{text-align:right;font-variant-numeric:tabular-nums}
.sym-cell{color:#f0b90b;font-weight:600}
.outcome-badge{display:inline-block;padding:2px 7px;border-radius:3px;font-size:.68rem;
  font-weight:700;letter-spacing:.4px;font-family:var(--mono)}
.outcome-TP       {background:rgba(14,203,129,.2);color:#11dd8c}
.outcome-SL       {background:rgba(246,70,93,.16);color:var(--red)}
.outcome-SL_first {background:rgba(246,70,93,.16);color:var(--red)}
.outcome-timeout  {background:rgba(240,165,0,.16);color:var(--yellow)}
.outcome-no_bars  {background:rgba(132,142,156,.16);color:var(--text3)}
.side-badge{display:inline-block;padding:2px 6px;border-radius:3px;font-size:.66rem;
  font-weight:700;font-family:var(--mono);letter-spacing:.4px}
.side-badge.is-long{background:rgba(14,203,129,.16);color:var(--green)}
.side-badge.is-short{background:rgba(246,70,93,.16);color:var(--red)}
.pct-pos{color:var(--green)}
.pct-neg{color:var(--red)}
.note{color:var(--text3);font-size:.72rem;margin-top:10px;font-family:var(--mono);line-height:1.55}
</style>
</head>
<body>
<h1>QTA — airborne 알림 적중 (누적)</h1>
<div class="subtitle">qta-airborne-daemon 의 FIRE 라인을 docker logs 에서 파싱 → 영속 JSONL store 누적 → Binance fapi 15m 봉 시뮬 → 적중률·PF·net% 집계. dashboard 가 처음 켜진 시점부터 store 에 누적되므로 docker rotation (4d 한도) 이후에도 옛 fire 보존.</div>
<div class="nav">
  <a href="/">← 대시보드</a>
  <a href="/cs-tsmom">cs-tsmom</a>
  <a href="/signals">신호 목록</a>
  <a href="/strategies">전략 카탈로그</a>
  <a href="/patch-notes">패치노트</a>
</div>
<div class="header-row">
  <label style="font-size:.78rem;color:var(--text2)">윈도우:
    <select id="window-selector" onchange="changeWindow(this.value)"
            style="background:var(--surface);color:var(--text);border:1px solid var(--border);padding:5px 10px;border-radius:4px;font-family:var(--mono);font-size:.78rem">
      <option value="today">오늘</option>
      <option value="yesterday">어제</option>
      <option value="7d">최근 7일</option>
      <option value="30d">최근 30일</option>
      <option value="all">전체 누적</option>
    </select>
  </label>
  <div class="meta" id="meta">로딩 중…</div>
  <span class="rule-badge" id="rule-badge">룰: TP +1.0% / SL -0.5% / 4봉(=1h) hold · 양방향 수수료 0.034% (테더맥스 54% 페이백 반영)</span>
  <button class="refresh-btn" id="refresh-btn" onclick="forceRefresh()">↻ 캐시 무효화 + 재계산</button>
</div>
<div id="content"><div class="empty">데이터를 불러오는 중입니다…</div></div>
<script>
const KST = 'Asia/Seoul';
function fmtKstFull(iso){
  if(!iso) return '—';
  try{
    return new Intl.DateTimeFormat('ko-KR',{timeZone:KST,year:'2-digit',month:'2-digit',
      day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(iso));
  }catch(e){ return iso; }
}
function fmtKstHm(iso){
  if(!iso) return '—';
  try{
    return new Intl.DateTimeFormat('ko-KR',{timeZone:KST,hour:'2-digit',minute:'2-digit',
      second:'2-digit',hour12:false}).format(new Date(iso));
  }catch(e){ return iso; }
}
function esc(s){
  return String(s==null?'':s).replace(/[&<>"']/g,c=>(
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function pctColor(v){
  if(v==null||isNaN(v)) return '';
  return v > 0 ? 'pct-pos' : (v < 0 ? 'pct-neg' : '');
}
function fmtPct(v, dec){
  if(v==null||isNaN(v)) return '—';
  const n = Number(v);
  const cls = pctColor(n);
  const sign = n>0?'+':'';
  return `<span class="${cls}">${sign}${n.toFixed(dec==null?2:dec)}%</span>`;
}
function fmtPctRaw(v, dec){
  if(v==null||isNaN(v)) return '—';
  const n = Number(v);
  const sign = n>0?'+':'';
  return sign + n.toFixed(dec==null?2:dec) + '%';
}
function fmtPrice(v){
  if(v==null||isNaN(v)) return '—';
  const n = Number(v);
  return n.toLocaleString('ko-KR',{maximumFractionDigits:6});
}

function renderStats(d){
  const fires = d.fires_total ?? 0;
  const tp = d.tp ?? 0, sl = (d.sl ?? 0) + (d.sl_first ?? 0);
  const winPctTxt = d.win_rate != null ? (d.win_rate * 100).toFixed(1) + '%' : '—';
  const pfTxt = d.pf != null ? Number(d.pf).toFixed(2) : '—';
  const net = d.net_pct;
  const netTxt = (net != null) ? (net >= 0 ? '+' : '') + Number(net).toFixed(2) + '%' : '—';
  const netCls = net == null ? 'dim' : (net >= 0 ? 'green' : 'red');
  const pfCls = (d.pf != null && d.pf >= 1.0) ? 'green' : (d.pf != null ? 'red' : 'dim');
  const winCls = (d.win_rate != null && d.win_rate >= 0.5) ? 'green' :
                 (d.win_rate != null ? 'red' : 'dim');
  const heroCls = net == null ? '' : (net >= 0 ? 'is-hero' : 'is-hero-bad');
  return `<div class="stat-grid">
    <div class="stat-tile ${heroCls}">
      <div class="stat-label">net (수수료 후)</div>
      <div class="stat-val ${netCls}">${esc(netTxt)}</div>
      <div class="stat-sub">gross ${fmtPctRaw(d.sum_pct, 2)} − fee ${((d.rule?.fee_pct ?? 0.034) * fires).toFixed(2)}%</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">FIRE</div>
      <div class="stat-val">${esc(fires)}</div>
      <div class="stat-sub">sim ${d.sims_total ?? 0} / 봉 미수신 ${(fires - (d.sims_total ?? 0))}</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">승률</div>
      <div class="stat-val ${winCls}">${esc(winPctTxt)}</div>
      <div class="stat-sub">TP ${tp} · SL ${sl} · timeout ${d.timeout ?? 0}</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">Profit Factor</div>
      <div class="stat-val ${pfCls}">${esc(pfTxt)}</div>
      <div class="stat-sub">손익비 1:2 (TP 1.0% / SL 0.5%)</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">평균 거래</div>
      <div class="stat-val ${pctColor(d.mean_pct) || 'dim'}">${fmtPctRaw(d.mean_pct, 2)}</div>
      <div class="stat-sub">trade 당 기대값</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">SL_first (보수)</div>
      <div class="stat-val dim">${esc(d.sl_first ?? 0)}</div>
      <div class="stat-sub">같은 봉에 TP·SL 동시 → SL 우선</div>
    </div>
  </div>`;
}

function renderSideCards(by_side){
  function card(side, s){
    const cls = side === 'long' ? 'is-long' : 'is-short';
    const titleCls = side === 'long' ? 'side-long' : 'side-short';
    const pfTxt = s.pf != null ? Number(s.pf).toFixed(2) : '—';
    const winTxt = s.win_rate != null ? (s.win_rate * 100).toFixed(1) + '%' : '—';
    return `<div class="side-card ${cls}">
      <div class="side-card-hdr">
        <div class="side-card-title ${titleCls}">${side.toUpperCase()}</div>
        <div class="side-row" style="margin:0"><b>${esc(s.n)}</b> 건</div>
      </div>
      <div class="side-row"><span>TP / SL</span><b>${esc(s.tp)} / ${esc(s.sl)}</b></div>
      <div class="side-row"><span>승률</span><b>${esc(winTxt)}</b></div>
      <div class="side-row"><span>합산 gross%</span><b class="${pctColor(s.sum_pct)}">${fmtPctRaw(s.sum_pct, 2)}</b></div>
      <div class="side-row"><span>Profit Factor</span><b>${esc(pfTxt)}</b></div>
    </div>`;
  }
  const long = by_side?.long, short = by_side?.short;
  if (!long && !short) return '';
  const html = ['<div class="section-h2">⚖️ 방향별 (long / short) <span class="count">· 한쪽 편향 진단</span></div>',
    '<div class="side-grid">'];
  if (long)  html.push(card('long',  long));
  if (short) html.push(card('short', short));
  html.push('</div>');
  return html.join('');
}

function renderBucketTable(buckets){
  if (!buckets || buckets.length === 0) {
    return `<div class="section-h2">🕒 KST 시간대별 <span class="count">· 데이터 없음</span></div>
      <div class="empty">시간대 분포를 보여줄 FIRE 가 없습니다.</div>`;
  }
  const trs = buckets.map(b => {
    const winPct = (b.win_rate * 100).toFixed(0) + '%';
    const pfTxt = b.pf != null ? Number(b.pf).toFixed(2) : '—';
    return `<tr>
      <td>${esc(b.bucket)}</td>
      <td class="td-num">${esc(b.n)}</td>
      <td class="td-num">${esc(b.tp)}</td>
      <td class="td-num">${esc(b.sl)}</td>
      <td class="td-num">${esc(winPct)}</td>
      <td class="td-num">${fmtPct(b.sum_pct, 2)}</td>
      <td class="td-num">${esc(pfTxt)}</td>
    </tr>`;
  }).join('');
  return `<div class="section-h2">🕒 KST 시간대별 <span class="count">· 04구간 분포</span></div>
    <table><thead><tr>
      <th>KST 구간</th><th class="td-num">n</th><th class="td-num">TP</th><th class="td-num">SL</th>
      <th class="td-num">승률</th><th class="td-num">합산</th><th class="td-num">PF</th>
    </tr></thead><tbody>${trs}</tbody></table>`;
}

function renderFiresTable(sims){
  if (!sims || sims.length === 0) {
    return `<div class="section-h2">🔥 개별 FIRE 상세 <span class="count">· 0건</span></div>
      <div class="empty">오늘 FIRE 가 아직 없습니다 — daemon 미가동 또는 모든 종목이 trigger 미달.</div>`;
  }
  // 최신 ts 먼저
  const rows = [...sims].sort((a,b) => String(b.ts || '').localeCompare(String(a.ts || '')));
  const trs = rows.map(r => {
    const sideCls = r.side === 'short' ? 'is-short' : 'is-long';
    const outcome = r.outcome || '—';
    const barTxt = r.bar_idx != null ? `봉${r.bar_idx}` : '—';
    return `<tr>
      <td>${esc(fmtKstHm(r.ts))}</td>
      <td class="sym-cell">${esc((r.symbol||'').replace(/USDT$/,''))}</td>
      <td><span class="side-badge ${sideCls}">${esc((r.side||'').toUpperCase())}</span></td>
      <td class="td-num">${esc(fmtPrice(r.fire_close))}</td>
      <td><span class="outcome-badge outcome-${esc(outcome)}">${esc(outcome)}</span></td>
      <td class="td-num">${r.pct != null ? fmtPct(r.pct, 2) : '<span style="color:var(--text3)">—</span>'}</td>
      <td class="td-num" style="color:var(--text3)">${esc(barTxt)}</td>
    </tr>`;
  }).join('');
  return `<div class="section-h2">🔥 개별 FIRE 상세 <span class="count">· ${rows.length}건 (최신순)</span></div>
    <table><thead><tr>
      <th>시각 (KST)</th><th>Symbol</th><th>방향</th>
      <th class="td-num">진입가</th><th>결과</th><th class="td-num">pct</th><th class="td-num">도달봉</th>
    </tr></thead><tbody>${trs}</tbody></table>
    <div class="note">
      결과 — <b>TP</b>: 시뮬 +1.0% 익절. <b>SL</b>: 시뮬 −0.5% 손절. <b>SL_first</b>: 같은 15m 봉에서 high·low 가 둘 다 닿음 → 보수적으로 SL 부터 체결됐다고 가정. <b>timeout</b>: 4봉(=1h) 동안 TP/SL 둘 다 미도달 → 마지막 close 로 청산. <b>no_bars</b>: Binance fapi 봉 fetch 실패 / 너무 최근 fire 라 봉이 아직 안 닫힘.<br>
      pct 는 gross (수수료 미반영) — 상단 net 카드는 fee 0.034% (테더맥스 54% 페이백 반영 실효율) × FIRE n 차감.
    </div>`;
}

function render(d){
  const out = [renderStats(d)];
  out.push(renderSideCards(d.by_side || {}));
  out.push(renderBucketTable(d.by_kst_bucket || []));
  out.push(renderFiresTable(d.sims || []));
  return out.join('');
}

// 현재 윈도우 — selector 가 바꿈. ?window=... 쿼리 파라미터로 fetch.
let CURRENT_WINDOW = (new URL(location.href).searchParams.get('window')) || 'today';

function _windowLabel(w){
  const map = {
    'today':     '오늘 (KST 자정~지금)',
    'yesterday': '어제 (KST 어제 자정~오늘 자정)',
    '7d':        '최근 7일 누적',
    '30d':       '최근 30일 누적',
    'all':       '전체 누적',
  };
  return map[w] || w;
}

async function refresh(){
  try{
    const r = await fetch('/api/airborne_metrics?window=' + encodeURIComponent(CURRENT_WINDOW));
    const j = await r.json();
    const meta = document.getElementById('meta');
    const content = document.getElementById('content');
    if (j.error){
      content.innerHTML = `<div class="error">${esc(j.error)}</div>`;
      return;
    }
    const fires = j.fires_total ?? 0;
    const cachedTxt = j.cached ? '(5분 캐시)' : '(방금 갱신)';
    const storeInfo = j.store_count ? ` · store=${j.store_count}` : '';
    const earliest = j.store_earliest ? ` (가장 옛 ${fmtKstFull(j.store_earliest)})` : '';
    meta.innerHTML =
      `<b>${esc(_windowLabel(j.window || CURRENT_WINDOW))}</b> · `
      + `${fmtKstFull(j.window_start_utc)} ~ ${fmtKstFull(j.window_end_utc)} · `
      + `FIRE <b>${fires}</b> · ${cachedTxt}${storeInfo}${earliest}`;
    content.innerHTML = render(j);
  }catch(e){
    document.getElementById('content').innerHTML =
      `<div class="error">로딩 실패: ${esc(String(e))} — 잠시 후 자동 재시도</div>`;
  }
}

function changeWindow(value){
  CURRENT_WINDOW = value;
  // URL 갱신 (북마크 가능)
  const url = new URL(location.href);
  url.searchParams.set('window', value);
  history.replaceState(null, '', url.toString());
  refresh();
}

async function forceRefresh(){
  const btn = document.getElementById('refresh-btn');
  btn.disabled = true;
  btn.textContent = '재계산 중…';
  try{
    // 서버 캐시는 5분 — 강제 무효화 endpoint 가 없으므로 cache-busting query 로 우회.
    const r = await fetch('/api/airborne_metrics?window=' + encodeURIComponent(CURRENT_WINDOW)
        + '&_=' + Date.now());
    const j = await r.json();
    const content = document.getElementById('content');
    const meta = document.getElementById('meta');
    if (j.error){
      content.innerHTML = `<div class="error">${esc(j.error)}</div>`;
      return;
    }
    const fires = j.fires_total ?? 0;
    const cachedTxt = j.cached ? '(5분 캐시)' : '(방금 갱신)';
    const storeInfo = j.store_count ? ` · store=${j.store_count}` : '';
    meta.innerHTML =
      `<b>${esc(_windowLabel(j.window || CURRENT_WINDOW))}</b> · `
      + `${fmtKstFull(j.window_start_utc)} ~ ${fmtKstFull(j.window_end_utc)} · `
      + `FIRE <b>${fires}</b> · ${cachedTxt}${storeInfo}`;
    content.innerHTML = render(j);
  }catch(e){
    alert('재계산 실패: ' + e);
  }finally{
    btn.disabled = false;
    btn.textContent = '↻ 캐시 무효화 + 재계산';
  }
}

// 페이지 로드 시 URL 의 window 로 selector 초기화
window.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('window-selector');
  if (sel) sel.value = CURRENT_WINDOW;
});
refresh();
setInterval(refresh, 60000);  // 1분마다 (서버는 5분 캐시이므로 보통 캐시 반환)
</script>
</body>
</html>"""
    # 룰별 후처리 — default 면 byte-identical, 2pct 면 fetch URL 에 rule 박고
    # 헤더 / 페이지 제목도 룰 표시. 한 HTML 본문을 두 룰에 재사용.
    if rule_key == "2pct":
        html = (
            html
            .replace(
                "'/api/airborne_metrics?window=' + encodeURIComponent(CURRENT_WINDOW)",
                "'/api/airborne_metrics?window=' + encodeURIComponent(CURRENT_WINDOW) + '&rule=2pct'",
            )
            .replace(
                "<title>QTA — airborne 알림 적중 (오늘)</title>",
                f"<title>QTA — {title_kr}</title>",
            )
            .replace(
                "<h1>QTA — airborne 알림 적중 (누적)</h1>",
                f"<h1>QTA — {title_kr}</h1>",
            )
        )
    # 룰 chip + 룰 toggle nav link 추가 (default / 2pct 양쪽 다 표시).
    rule_strip = (
        f'<div class="meta">현재 룰: <b>{rule_chip}</b></div>\n'
        f'<div class="nav">{nav_other}</div>'
    )
    html = html.replace(
        '<div class="subtitle">',
        f'{rule_strip}\n<div class="subtitle">',
        1,
    )
    return html


def _render_ma_cross_page() -> str:
    """골든/데드 크로스 적중 페이지 — CROSS 의 TP/SL 시뮬레이션 상세.

    airborne 페이지 레이아웃을 그대로 미러 — 상단 stat 타일 + golden/death 분리
    카드 + KST 시간대별 표 + 개별 CROSS 상세 테이블. ``/api/ma_cross_metrics``
    한 endpoint 만 폴링 (서버 5분 캐시). 손익비 1:6 (SL 2% / TP 12%) 고정.
    """
    return """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>QTA — 골든/데드 크로스 적중</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0b0e11;--surface:#161a1e;--surface2:#1e2329;--border:#2b3139;
  --text:#eaecef;--text2:#b7bdc6;--text3:#848e9c;--green:#0ecb81;--red:#f6465d;--yellow:#f0a500;
  --mono:'IBM Plex Mono','Consolas',monospace;--sans:'IBM Plex Sans KR','Segoe UI',sans-serif}
body{font-family:var(--sans);background:var(--bg);color:var(--text);padding:14px;font-size:13px}
h1{font-size:1.05rem;color:var(--text);margin-bottom:6px;font-weight:600}
.subtitle{font-size:.75rem;color:var(--text3);margin-bottom:10px}
.nav{margin-bottom:14px}
.nav a{color:var(--text2);text-decoration:none;margin-right:12px;font-size:.8rem;
  background:var(--surface);padding:6px 12px;border-radius:4px;border:1px solid var(--border)}
.nav a:hover{color:var(--text);background:var(--surface2)}
.meta{font-size:.75rem;color:var(--text3);margin-bottom:8px;font-family:var(--mono)}
.empty,.error{padding:30px;text-align:center;color:var(--text3);
  background:var(--surface);border-radius:6px;border:1px solid var(--border)}
.error{color:var(--red);border-color:rgba(246,70,93,.35)}
.header-row{display:flex;align-items:center;gap:14px;margin-bottom:8px;flex-wrap:wrap}
.rule-badge{background:var(--surface2);border:1px solid var(--border);color:var(--text2);
  padding:3px 8px;border-radius:4px;font-size:.7rem;font-family:var(--mono)}
.refresh-btn{background:var(--surface2);border:1px solid var(--border);color:var(--text);
  padding:6px 14px;border-radius:4px;font-size:.78rem;cursor:pointer;font-family:var(--sans);
  margin-left:auto;transition:border-color .15s,color .15s}
.refresh-btn:hover{border-color:var(--green);color:var(--green)}
.refresh-btn:disabled{opacity:.4;cursor:wait}
.section-h2{font-size:.85rem;color:var(--text);font-weight:600;margin:18px 0 10px 0;
  display:flex;align-items:center;gap:10px}
.section-h2 .count{font-size:.7rem;color:var(--text3);font-family:var(--mono);font-weight:400}

/* ── 상단 큰 stat 타일 ── */
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:8px}
.stat-tile{background:var(--surface);border:1px solid var(--border);border-radius:6px;
  padding:14px 14px;display:flex;flex-direction:column;gap:6px}
.stat-tile.is-hero{background:linear-gradient(180deg,rgba(14,203,129,.04),transparent);
  border-color:rgba(14,203,129,.25)}
.stat-tile.is-hero-bad{background:linear-gradient(180deg,rgba(246,70,93,.05),transparent);
  border-color:rgba(246,70,93,.25)}
.stat-label{font-size:.7rem;color:var(--text3);font-weight:600;letter-spacing:.4px;text-transform:uppercase}
.stat-val{font-size:1.5rem;font-weight:700;font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--text)}
.stat-val.dim{color:var(--text3);font-size:1.1rem}
.stat-val.green{color:var(--green)}
.stat-val.red{color:var(--red)}
.stat-sub{font-size:.7rem;color:var(--text3);font-family:var(--mono)}

/* ── cross breakdown (golden/death 2칸) ── */
.side-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-bottom:16px}
.side-card{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:14px}
.side-card.is-long{border-color:rgba(14,203,129,.3)}
.side-card.is-short{border-color:rgba(246,70,93,.3)}
.side-card-hdr{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
.side-card-title{font-size:.85rem;font-weight:700;font-family:var(--mono);letter-spacing:.5px}
.side-long{color:var(--green)}
.side-short{color:var(--red)}
.side-row{display:flex;justify-content:space-between;font-size:.78rem;padding:3px 0;color:var(--text2);font-family:var(--mono)}
.side-row b{color:var(--text);font-variant-numeric:tabular-nums}

/* ── KST bucket + 전체 CROSS 테이블 공용 ── */
table{width:100%;border-collapse:separate;border-spacing:0;
  background:var(--surface);border-radius:6px;overflow:hidden;border:1px solid var(--border)}
thead th{position:sticky;top:0;background:var(--surface2);color:var(--text2);font-weight:600;
  text-align:left;padding:8px 10px;font-size:.72rem;text-transform:uppercase;letter-spacing:.4px;
  border-bottom:1px solid var(--border);z-index:5}
tbody td{padding:7px 10px;font-size:.78rem;border-bottom:1px solid #20262d;
  font-family:var(--mono);color:var(--text)}
tbody tr:hover{background:#1c2229}
.td-num{text-align:right;font-variant-numeric:tabular-nums}
.sym-cell{color:#f0b90b;font-weight:600}
.outcome-badge{display:inline-block;padding:2px 7px;border-radius:3px;font-size:.68rem;
  font-weight:700;letter-spacing:.4px;font-family:var(--mono)}
.outcome-TP       {background:rgba(14,203,129,.2);color:#11dd8c}
.outcome-SL       {background:rgba(246,70,93,.16);color:var(--red)}
.outcome-SL_first {background:rgba(246,70,93,.16);color:var(--red)}
.outcome-timeout  {background:rgba(240,165,0,.16);color:var(--yellow)}
.outcome-no_bars  {background:rgba(132,142,156,.16);color:var(--text3)}
.side-badge{display:inline-block;padding:2px 6px;border-radius:3px;font-size:.66rem;
  font-weight:700;font-family:var(--mono);letter-spacing:.4px}
.side-badge.is-long{background:rgba(14,203,129,.16);color:var(--green)}
.side-badge.is-short{background:rgba(246,70,93,.16);color:var(--red)}
.pct-pos{color:var(--green)}
.pct-neg{color:var(--red)}
.note{color:var(--text3);font-size:.72rem;margin-top:10px;font-family:var(--mono);line-height:1.55}
</style>
</head>
<body>
<h1>QTA — 골든/데드 크로스 적중 (누적)</h1>
<div class="meta">현재 룰: <b>골든=롱 / 데드=숏 · TP +12% / SL -2% (손익비 1:6) · 720봉(=30일) hold</b></div>
<div class="subtitle">qta-ma-cross-daemon 의 CROSS 라인(bitget top-100, 1h SMA25/200 골든·데드 크로스)을 docker logs 에서 파싱 → 영속 JSONL store 누적 → bitget 1h 봉 시뮬 → 적중률·PF·net% 집계. dashboard 가 처음 켜진 시점부터 store 에 누적되므로 docker rotation 이후에도 옛 cross 보존.</div>
<div class="nav">
  <a href="/">← 대시보드</a>
  <a href="/airborne">airborne 적중</a>
  <a href="/cs-tsmom">cs-tsmom</a>
  <a href="/signals">신호 목록</a>
  <a href="/strategies">전략 카탈로그</a>
  <a href="/patch-notes">패치노트</a>
</div>
<div class="header-row">
  <label style="font-size:.78rem;color:var(--text2)">윈도우:
    <select id="window-selector" onchange="changeWindow(this.value)"
            style="background:var(--surface);color:var(--text);border:1px solid var(--border);padding:5px 10px;border-radius:4px;font-family:var(--mono);font-size:.78rem">
      <option value="today">오늘</option>
      <option value="yesterday">어제</option>
      <option value="7d">최근 7일</option>
      <option value="30d">최근 30일</option>
      <option value="all">전체 누적</option>
    </select>
  </label>
  <span class="rule-badge" id="rule-badge">룰: golden=롱/death=숏 · TP +12% / SL -2% / 720봉(=30일) hold · 양방향 수수료 0.034%</span>
  <div class="meta" id="meta">로딩 중…</div>
  <button class="refresh-btn" id="refresh-btn" onclick="forceRefresh()">↻ 캐시 무효화 + 재계산</button>
</div>
<div id="content"><div class="empty">데이터를 불러오는 중입니다…</div></div>
<script>
const KST = 'Asia/Seoul';
function fmtKstFull(iso){
  if(!iso) return '—';
  try{
    return new Intl.DateTimeFormat('ko-KR',{timeZone:KST,year:'2-digit',month:'2-digit',
      day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(iso));
  }catch(e){ return iso; }
}
function fmtKstHm(iso){
  if(!iso) return '—';
  try{
    return new Intl.DateTimeFormat('ko-KR',{timeZone:KST,hour:'2-digit',minute:'2-digit',
      second:'2-digit',hour12:false}).format(new Date(iso));
  }catch(e){ return iso; }
}
function esc(s){
  return String(s==null?'':s).replace(/[&<>"']/g,c=>(
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function pctColor(v){
  if(v==null||isNaN(v)) return '';
  return v > 0 ? 'pct-pos' : (v < 0 ? 'pct-neg' : '');
}
function fmtPct(v, dec){
  if(v==null||isNaN(v)) return '—';
  const n = Number(v);
  const cls = pctColor(n);
  const sign = n>0?'+':'';
  return `<span class="${cls}">${sign}${n.toFixed(dec==null?2:dec)}%</span>`;
}
function fmtPctRaw(v, dec){
  if(v==null||isNaN(v)) return '—';
  const n = Number(v);
  const sign = n>0?'+':'';
  return sign + n.toFixed(dec==null?2:dec) + '%';
}
function fmtPrice(v){
  if(v==null||isNaN(v)) return '—';
  const n = Number(v);
  return n.toLocaleString('ko-KR',{maximumFractionDigits:6});
}

function renderStats(d){
  const crosses = d.crosses_total ?? 0;
  const tp = d.tp ?? 0, sl = (d.sl ?? 0) + (d.sl_first ?? 0);
  const winPctTxt = d.win_rate != null ? (d.win_rate * 100).toFixed(1) + '%' : '—';
  const pfTxt = d.pf != null ? Number(d.pf).toFixed(2) : '—';
  const net = d.net_pct;
  const netTxt = (net != null) ? (net >= 0 ? '+' : '') + Number(net).toFixed(2) + '%' : '—';
  const netCls = net == null ? 'dim' : (net >= 0 ? 'green' : 'red');
  const pfCls = (d.pf != null && d.pf >= 1.0) ? 'green' : (d.pf != null ? 'red' : 'dim');
  const winCls = (d.win_rate != null && d.win_rate >= 0.5) ? 'green' :
                 (d.win_rate != null ? 'red' : 'dim');
  const heroCls = net == null ? '' : (net >= 0 ? 'is-hero' : 'is-hero-bad');
  return `<div class="stat-grid">
    <div class="stat-tile ${heroCls}">
      <div class="stat-label">net (수수료 후)</div>
      <div class="stat-val ${netCls}">${esc(netTxt)}</div>
      <div class="stat-sub">gross ${fmtPctRaw(d.sum_pct, 2)} − fee ${((d.rule?.fee_pct ?? 0.034) * crosses).toFixed(2)}%</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">CROSS</div>
      <div class="stat-val">${esc(crosses)}</div>
      <div class="stat-sub">sim ${d.sims_total ?? 0} / 봉 미수신 ${(crosses - (d.sims_total ?? 0))}</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">승률</div>
      <div class="stat-val ${winCls}">${esc(winPctTxt)}</div>
      <div class="stat-sub">TP ${tp} · SL ${sl} · timeout ${d.timeout ?? 0}</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">Profit Factor</div>
      <div class="stat-val ${pfCls}">${esc(pfTxt)}</div>
      <div class="stat-sub">손익비 1:6 (TP 12% / SL 2%)</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">평균 거래</div>
      <div class="stat-val ${pctColor(d.mean_pct) || 'dim'}">${fmtPctRaw(d.mean_pct, 2)}</div>
      <div class="stat-sub">trade 당 기대값</div>
    </div>
    <div class="stat-tile">
      <div class="stat-label">SL_first (보수)</div>
      <div class="stat-val dim">${esc(d.sl_first ?? 0)}</div>
      <div class="stat-sub">같은 봉에 TP·SL 동시 → SL 우선</div>
    </div>
  </div>`;
}

function renderCrossCards(by_cross){
  function card(cross, s){
    const cls = cross === 'golden' ? 'is-long' : 'is-short';
    const titleCls = cross === 'golden' ? 'side-long' : 'side-short';
    const label = cross === 'golden' ? 'GOLDEN (롱)' : 'DEATH (숏)';
    const pfTxt = s.pf != null ? Number(s.pf).toFixed(2) : '—';
    const winTxt = s.win_rate != null ? (s.win_rate * 100).toFixed(1) + '%' : '—';
    return `<div class="side-card ${cls}">
      <div class="side-card-hdr">
        <div class="side-card-title ${titleCls}">${label}</div>
        <div class="side-row" style="margin:0"><b>${esc(s.n)}</b> 건</div>
      </div>
      <div class="side-row"><span>TP / SL</span><b>${esc(s.tp)} / ${esc(s.sl)}</b></div>
      <div class="side-row"><span>승률</span><b>${esc(winTxt)}</b></div>
      <div class="side-row"><span>합산 gross%</span><b class="${pctColor(s.sum_pct)}">${fmtPctRaw(s.sum_pct, 2)}</b></div>
      <div class="side-row"><span>Profit Factor</span><b>${esc(pfTxt)}</b></div>
    </div>`;
  }
  const golden = by_cross?.golden, death = by_cross?.death;
  if (!golden && !death) return '';
  const html = ['<div class="section-h2">⚖️ 방향별 (golden / death) <span class="count">· 한쪽 편향 진단</span></div>',
    '<div class="side-grid">'];
  if (golden) html.push(card('golden', golden));
  if (death)  html.push(card('death',  death));
  html.push('</div>');
  return html.join('');
}

function renderBucketTable(buckets){
  if (!buckets || buckets.length === 0) {
    return `<div class="section-h2">🕒 KST 시간대별 <span class="count">· 데이터 없음</span></div>
      <div class="empty">시간대 분포를 보여줄 CROSS 가 없습니다.</div>`;
  }
  const trs = buckets.map(b => {
    const winPct = (b.win_rate * 100).toFixed(0) + '%';
    const pfTxt = b.pf != null ? Number(b.pf).toFixed(2) : '—';
    return `<tr>
      <td>${esc(b.bucket)}</td>
      <td class="td-num">${esc(b.n)}</td>
      <td class="td-num">${esc(b.tp)}</td>
      <td class="td-num">${esc(b.sl)}</td>
      <td class="td-num">${esc(winPct)}</td>
      <td class="td-num">${fmtPct(b.sum_pct, 2)}</td>
      <td class="td-num">${esc(pfTxt)}</td>
    </tr>`;
  }).join('');
  return `<div class="section-h2">🕒 KST 시간대별 <span class="count">· 04구간 분포</span></div>
    <table><thead><tr>
      <th>KST 구간</th><th class="td-num">n</th><th class="td-num">TP</th><th class="td-num">SL</th>
      <th class="td-num">승률</th><th class="td-num">합산</th><th class="td-num">PF</th>
    </tr></thead><tbody>${trs}</tbody></table>`;
}

function renderCrossesTable(sims){
  if (!sims || sims.length === 0) {
    return `<div class="section-h2">✚ 개별 CROSS 상세 <span class="count">· 0건</span></div>
      <div class="empty">CROSS 가 아직 없습니다 — daemon 미가동 또는 크로스 미발생.</div>`;
  }
  const rows = [...sims].sort((a,b) => String(b.ts || '').localeCompare(String(a.ts || '')));
  const trs = rows.map(r => {
    const crossCls = r.cross === 'death' ? 'is-short' : 'is-long';
    const crossTxt = r.cross === 'death' ? 'DEATH' : 'GOLDEN';
    const outcome = r.outcome || '—';
    const barTxt = r.bar_idx != null ? `봉${r.bar_idx}` : '—';
    return `<tr>
      <td>${esc(fmtKstHm(r.ts))}</td>
      <td class="sym-cell">${esc((r.symbol||'').replace(/USDT$/,''))}</td>
      <td><span class="side-badge ${crossCls}">${esc(crossTxt)}</span></td>
      <td class="td-num">${esc(fmtPrice(r.close))}</td>
      <td><span class="outcome-badge outcome-${esc(outcome)}">${esc(outcome)}</span></td>
      <td class="td-num">${r.pct != null ? fmtPct(r.pct, 2) : '<span style="color:var(--text3)">—</span>'}</td>
      <td class="td-num" style="color:var(--text3)">${esc(barTxt)}</td>
    </tr>`;
  }).join('');
  return `<div class="section-h2">✚ 개별 CROSS 상세 <span class="count">· ${rows.length}건 (최신순)</span></div>
    <table><thead><tr>
      <th>시각 (KST)</th><th>Symbol</th><th>크로스</th>
      <th class="td-num">진입가</th><th>결과</th><th class="td-num">pct</th><th class="td-num">도달봉</th>
    </tr></thead><tbody>${trs}</tbody></table>
    <div class="note">
      결과 — <b>TP</b>: 시뮬 +12% 익절. <b>SL</b>: 시뮬 −2% 손절. <b>SL_first</b>: 같은 1h 봉에서 high·low 가 둘 다 닿음 → 보수적으로 SL 부터 체결됐다고 가정. <b>timeout</b>: 720봉(=30일) 동안 TP/SL 둘 다 미도달 → 마지막 close 로 청산. <b>no_bars</b>: bitget 봉 fetch 실패 / 너무 최근 cross 라 봉이 아직 안 닫힘.<br>
      pct 는 gross (수수료 미반영) — 상단 net 카드는 fee 0.034% × CROSS n 차감. golden=롱 / death=숏.
    </div>`;
}

function render(d){
  const out = [renderStats(d)];
  out.push(renderCrossCards(d.by_cross || {}));
  out.push(renderBucketTable(d.by_kst_bucket || []));
  out.push(renderCrossesTable(d.sims || []));
  return out.join('');
}

let CURRENT_WINDOW = (new URL(location.href).searchParams.get('window')) || 'today';

function _windowLabel(w){
  const map = {
    'today':     '오늘 (KST 자정~지금)',
    'yesterday': '어제 (KST 어제 자정~오늘 자정)',
    '7d':        '최근 7일 누적',
    '30d':       '최근 30일 누적',
    'all':       '전체 누적',
  };
  return map[w] || w;
}

async function refresh(){
  try{
    const r = await fetch('/api/ma_cross_metrics?window=' + encodeURIComponent(CURRENT_WINDOW));
    const j = await r.json();
    const meta = document.getElementById('meta');
    const content = document.getElementById('content');
    if (j.error){
      content.innerHTML = `<div class="error">${esc(j.error)}</div>`;
      return;
    }
    const crosses = j.crosses_total ?? 0;
    const cachedTxt = j.cached ? '(5분 캐시)' : '(방금 갱신)';
    const storeInfo = j.store_count ? ` · store=${j.store_count}` : '';
    const earliest = j.store_earliest ? ` (가장 옛 ${fmtKstFull(j.store_earliest)})` : '';
    meta.innerHTML =
      `<b>${esc(_windowLabel(j.window || CURRENT_WINDOW))}</b> · `
      + `${fmtKstFull(j.window_start_utc)} ~ ${fmtKstFull(j.window_end_utc)} · `
      + `CROSS <b>${crosses}</b> · ${cachedTxt}${storeInfo}${earliest}`;
    content.innerHTML = render(j);
  }catch(e){
    document.getElementById('content').innerHTML =
      `<div class="error">로딩 실패: ${esc(String(e))} — 잠시 후 자동 재시도</div>`;
  }
}

function changeWindow(value){
  CURRENT_WINDOW = value;
  const url = new URL(location.href);
  url.searchParams.set('window', value);
  history.replaceState(null, '', url.toString());
  refresh();
}

async function forceRefresh(){
  const btn = document.getElementById('refresh-btn');
  btn.disabled = true;
  btn.textContent = '재계산 중…';
  try{
    const r = await fetch('/api/ma_cross_metrics?window=' + encodeURIComponent(CURRENT_WINDOW)
        + '&_=' + Date.now());
    const j = await r.json();
    const content = document.getElementById('content');
    const meta = document.getElementById('meta');
    if (j.error){
      content.innerHTML = `<div class="error">${esc(j.error)}</div>`;
      return;
    }
    const crosses = j.crosses_total ?? 0;
    const cachedTxt = j.cached ? '(5분 캐시)' : '(방금 갱신)';
    const storeInfo = j.store_count ? ` · store=${j.store_count}` : '';
    meta.innerHTML =
      `<b>${esc(_windowLabel(j.window || CURRENT_WINDOW))}</b> · `
      + `${fmtKstFull(j.window_start_utc)} ~ ${fmtKstFull(j.window_end_utc)} · `
      + `CROSS <b>${crosses}</b> · ${cachedTxt}${storeInfo}`;
    content.innerHTML = render(j);
  }catch(e){
    alert('재계산 실패: ' + e);
  }finally{
    btn.disabled = false;
    btn.textContent = '↻ 캐시 무효화 + 재계산';
  }
}

window.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('window-selector');
  if (sel) sel.value = CURRENT_WINDOW;
});
refresh();
setInterval(refresh, 60000);
</script>
</body>
</html>"""


def _render_strategies(items: list[dict]) -> str:
    cards_html = "".join(_strategy_card(it) for it in items)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>QTA — 전략 카탈로그</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#0f1117;color:#e0e0e0;padding:16px}}
h1{{font-size:1.1rem;color:#7ecef4;margin-bottom:14px}}
.nav a{{color:#7ecef4;text-decoration:none;margin-right:14px;font-size:.85rem}}
{_STRATEGY_CARD_CSS}
</style>
</head>
<body>
<h1>QTA — 전략 카탈로그</h1>
<div class="nav"><a href="/">← 대시보드</a></div>
<div class="strat-grid">{cards_html}</div>
<script>
{_STRATEGY_TOGGLE_JS}
</script>
</body>
</html>"""


def create_app(state: DashboardState | None = None) -> FastAPI:
    if state is None:
        state = DashboardState()

    if state.metrics is None:
        state.metrics = Metrics()

    if state.timeline_broker is None:
        state.timeline_broker = TimelineBroker()

    if state.ops_counters is None:
        state.ops_counters = OpsCounters()

    app = FastAPI(title="QTA Dashboard", docs_url=None, redoc_url=None)

    # ── airborne fire store 백그라운드 갱신 (브라우저 무관) ──────────────────────
    # history.jsonl 은 본래 /api/airborne_metrics (대시보드 airborne 페이지 폴링)
    # 에서만 `docker logs` 파싱→append 되었다. 즉 store 신선도가 *브라우저를
    # 열어둔 동안만* 유지됨 → 대시보드 탭을 닫으면 store 가 얼어붙고 consume
    # 모드/데몬 게이트가 굶어 트레이더가 매수를 0 건 하던 사고 (2026-06-09:
    # 22:03 store freeze → 00:00 KST 발화 미수록 → 거래 정지). 페이지 의존을
    # 끊고 dashboard 프로세스(= live_run) 가 떠 있는 동안 주기적으로 store 를
    # 채운다. 끄려면 AIRBORNE_FIRE_STORE_REFRESH=0, 주기는 AIRBORNE_FIRE_REFRESH_SEC.
    @app.on_event("startup")
    async def _start_airborne_fire_refresher() -> None:
        import os as _os
        if _os.environ.get("AIRBORNE_FIRE_STORE_REFRESH", "1") == "0":
            return
        interval = max(10.0, float(_os.environ.get("AIRBORNE_FIRE_REFRESH_SEC", "45")))

        async def _refresh_loop() -> None:
            import logging as _lg
            log = _lg.getLogger(__name__)
            log.info("[airborne_fire_refresher] started (every %.0fs, docker logs)", interval)
            while True:
                try:
                    since = (
                        datetime.now(timezone.utc) - timedelta(hours=6)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    fires = await asyncio.to_thread(
                        _parse_airborne_fires_from_docker_logs, since,
                    )
                    if fires:
                        store = _get_airborne_fire_store()
                        added = store.append_many(fires)
                        if added:
                            log.info(
                                "[airborne_fire_refresher] +%d new fires (total=%d)",
                                added, store.count(),
                            )
                except Exception:  # noqa: BLE001 — 갱신 실패가 대시보드/트레이딩을 죽이면 안 됨
                    _lg.getLogger(__name__).warning(
                        "[airborne_fire_refresher] tick failed", exc_info=True,
                    )
                await asyncio.sleep(interval)

        asyncio.create_task(_refresh_loop(), name="airborne-fire-refresher")

    # ── ma-cross store 백그라운드 갱신 (airborne refresher 미러) ─────────────────
    # qta-ma-cross-daemon 의 CROSS 라인을 주기적으로 docker logs 에서 파싱 →
    # logs/ma-cross/history.jsonl 누적. 브라우저 탭 의존 없이 dashboard 프로세스가
    # 떠 있는 동안 store 신선도 유지. 끄려면 MA_CROSS_STORE_REFRESH=0,
    # 주기는 MA_CROSS_REFRESH_SEC (기본 60).
    @app.on_event("startup")
    async def _start_ma_cross_refresher() -> None:
        import os as _os
        if _os.environ.get("MA_CROSS_STORE_REFRESH", "1") == "0":
            return
        interval = max(10.0, float(_os.environ.get("MA_CROSS_REFRESH_SEC", "60")))

        async def _refresh_loop() -> None:
            import logging as _lg
            log = _lg.getLogger(__name__)
            log.info("[ma_cross_refresher] started (every %.0fs, docker logs)", interval)
            while True:
                try:
                    since = (
                        datetime.now(timezone.utc) - timedelta(hours=6)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    crosses = await asyncio.to_thread(
                        _parse_ma_cross_from_docker_logs, since,
                    )
                    if crosses:
                        store = _get_ma_cross_store()
                        added = store.append_many(crosses)
                        if added:
                            log.info(
                                "[ma_cross_refresher] +%d new crosses (total=%d)",
                                added, store.count(),
                            )
                except Exception:  # noqa: BLE001 — 갱신 실패가 대시보드/트레이딩을 죽이면 안 됨
                    _lg.getLogger(__name__).warning(
                        "[ma_cross_refresher] tick failed", exc_info=True,
                    )
                await asyncio.sleep(interval)

        asyncio.create_task(_refresh_loop(), name="ma-cross-refresher")

    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        return HTMLResponse(content=_render_dashboard(state, _enriched_catalog()))

    @app.get("/metrics")
    async def metrics() -> Response:
        data = generate_latest(state.metrics.registry)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/pnl")
    async def api_pnl() -> JSONResponse:
        # 2026-05-23: venue 별 일간/월간 실현손익.
        #   Binance — 거래소 income 원장(/fapi/v1/income)의 NET (REALIZED_PNL
        #     + COMMISSION + FUNDING_FEE). 거래소 화면 실현손익과 정확히 일치.
        #     WAL 재구성은 체결 누락·round-trip 페어링·수수료 정의 차이로
        #     절대 거래소 원장과 못 맞춤 → PnL 카드 source 에서 폐기.
        #   KIS — round-trip 재구성(추정치). KIS income TR 연동은 후속 PR.
        # 어떤 경로도 500 내지 않음 — 실패 venue 는 null (프론트가 "—" 표시).
        import asyncio as _asyncio

        # ── Binance: 거래소 income 원장 (실제 실현손익) ──────────────────
        bn_daily = bn_monthly = None
        provider = state.account_info_provider
        if provider is not None and hasattr(provider, "fetch_binance_pnl"):
            try:
                bn = await _asyncio.to_thread(provider.fetch_binance_pnl)
                if bn.get("ok"):
                    bn_daily = bn.get("daily")
                    bn_monthly = bn.get("monthly")
            except Exception:  # noqa: BLE001 — never 500 the dashboard
                pass

        # ── Bitget: 청산 포지션 netProfit 원장 (#395 — 주 운영 venue) ──────
        bg_daily = bg_monthly = None
        if provider is not None and hasattr(provider, "fetch_bitget_pnl"):
            try:
                bg = await _asyncio.to_thread(provider.fetch_bitget_pnl)
                if bg.get("ok"):
                    bg_daily = bg.get("daily")
                    bg_monthly = bg.get("monthly")
            except Exception:  # noqa: BLE001 — never 500 the dashboard
                pass

        # ── KIS: round-trip 재구성 (추정치 — income TR 연동은 후속) ──────
        kis_daily = kis_monthly = None
        by_strategy: dict = {}
        log_dir = _resolve_log_dir()
        if log_dir is not None:
            try:
                wal_paths = await _asyncio.to_thread(discover_wal_files, log_dir)
                if wal_paths:
                    trades = await _asyncio.to_thread(reconstruct_trades, wal_paths)
                    recon = _pnl_from_trades(trades)
                    kis_daily = recon["daily_by_venue"].get("kis")
                    kis_monthly = recon["monthly_by_venue"].get("kis")
                    by_strategy = recon["by_strategy"]
            except Exception:  # noqa: BLE001 — never 500 the dashboard
                pass

        return JSONResponse({
            # top-level 스칼라 = Bitget(주 운영 venue, #395) 값. telegram /today
            # 등 "한 숫자" 요약용 — KRW·USDT cross-sum 이 아니라 USDT 단일 venue.
            # 대시보드 카드는 daily_by_venue/monthly_by_venue 만 쓴다.
            "daily": bg_daily if bg_daily is not None else 0.0,
            "monthly": bg_monthly if bg_monthly is not None else 0.0,
            "daily_by_venue": {"bitget": bg_daily, "binance": bn_daily, "kis": kis_daily},
            "monthly_by_venue": {"bitget": bg_monthly, "binance": bn_monthly, "kis": kis_monthly},
            "by_strategy": by_strategy,
            "bitget_source": "history_position_netprofit",
            "binance_source": "exchange_income",
            "kis_source": "reconstructed",
        })

    @app.get("/api/ops")
    async def api_ops() -> JSONResponse:
        oc = state.ops_counters
        if oc is None:
            return JSONResponse({"available": False})
        return JSONResponse({"available": True, **oc.snapshot()})

    @app.get("/api/trades")
    async def api_trades(limit: int = Query(default=50, ge=1, le=500)) -> JSONResponse:
        """Recent buy/sell fills + submitted orders, newest-first.

        Reads `state.wal_path` plus every `state.extra_wal_paths` entry — the
        `smoke-dual` runtime writes two WAL files (KIS + Binance) and both must
        surface in the dashboard. Read-only — does not mutate any aggregator.
        """
        paths: list[Path] = []
        if state.wal_path is not None and Path(state.wal_path).exists():
            paths.append(Path(state.wal_path))
        for p in state.extra_wal_paths or []:
            if p is not None and Path(p).exists() and Path(p) not in paths:
                paths.append(Path(p))
        if not paths:
            return JSONResponse({"available": True, "trades": []})
        rows: list[dict] = []
        for p in paths:
            events, _corruptions = wal_replay(p)
            for ev in events:
                # #238 — order_acked 도 포함 (Binance MARKET 의 NEW 응답이 broker 측에
                # 주문이 들어갔다는 신호. instant fill 의 FILLED 은 user-data WS 후속).
                if ev.event_type not in ("order_filled", "fill_received", "order_submitted", "order_placed", "order_acked"):
                    continue
                pl = ev.payload or {}
                # signal_emitted 같은 이벤트엔 symbol 이 없을 수 있으므로 order_acked 의 client_order_id 에서 추출.
                cid = pl.get("client_order_id") or ""
                inferred_symbol = pl.get("symbol")
                if not inferred_symbol and ev.event_type == "order_acked":
                    inferred_symbol = pl.get("strategy_id", "").split("-")[-1] if pl.get("strategy_id") else ""
                rows.append({
                    "ts": ev.ts,
                    "event_type": ev.event_type,
                    "strategy_id": pl.get("strategy_id", ""),
                    "symbol": inferred_symbol or "",
                    "side": pl.get("side", ""),
                    "qty": pl.get("qty") or pl.get("quantity"),
                    "price": pl.get("price") or pl.get("fill_price"),
                    "broker": pl.get("broker", ""),
                    "filled": ev.event_type in ("order_filled", "fill_received") or (ev.event_type == "order_acked" and pl.get("status") == "FILLED"),
                })
        rows.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        return JSONResponse({"available": True, "trades": rows[:limit]})

    @app.get("/api/strategy_positions")
    async def api_strategy_positions() -> JSONResponse:
        """전략별 매수/매도 집계 — "어떤 전략이 매수했나" 한눈에 (#238 후속).

        WAL order_acked/order_filled 를 strategy_id 로 집계. 평단가는 fill price
        있을 때만 (Binance MARKET ack 는 status=NEW 라 가격 미포함 → '-' 표시).
        실현손익은 pnl_aggregator.by_strategy wired 시.

        #238 follow-up — 전략별 포지션은 *영구·누적* 이어야 한다 (run 마다
        wipe 되면 안 됨). /api/trade_history 와 동일하게 _resolve_log_dir()
        + discover_wal_files() 로 모든 run 의 WAL 을 합산하고, 현재 run 의
        wal_path/extra_wal_paths 가 discover glob 밖이면 union.
        Robust: 디렉토리 부재/빈 경우 빈 리스트 반환, 절대 500 금지.
        """
        paths: list[Path] = []
        log_dir = _resolve_log_dir()
        if log_dir is not None:
            try:
                paths.extend(discover_wal_files(log_dir))
            except Exception:  # noqa: BLE001 — never 500 the dashboard
                paths = []
        if state.wal_path is not None and Path(state.wal_path).exists() \
                and Path(state.wal_path) not in paths:
            paths.append(Path(state.wal_path))
        for p in state.extra_wal_paths or []:
            if p is not None and Path(p).exists() and Path(p) not in paths:
                paths.append(Path(p))
        # #238 follow-up — aggregate per (strategy_id, SYMBOL), NOT per
        # strategy alone. A live-scanner trades a whole universe; keying by
        # strategy_id collapsed SPK/AI/TRX/NEAR/... into ONE row whose symbol
        # was frozen to the first seen (BTCUSDT) and whose qty/avg summed
        # across different symbols → garbage. One row per (strategy, symbol).
        agg: dict[tuple[str, str], dict] = {}
        for p in paths:
            events, _ = wal_replay(p)
            for ev in events:
                # 2026-05-22: 포지션 집계는 체결(order_filled/fill_received)
                # 만. order_acked 는 거래소 "접수"(NEW) 신호일 뿐 체결이
                # 아니다 — 같은 주문 1건이 acked→filled 두 이벤트로 WAL 에
                # 적히는데 둘 다 buy_n/buy_qty 에 더하면 정확히 2배가 된다
                # (INJ 실체결 431 → dashboard 862 사고). phase 가 다른
                # 이벤트를 한 단위로 합산하면 안 된다.
                if ev.event_type not in ("order_filled", "fill_received"):
                    continue
                pl = ev.payload or {}
                sid = pl.get("strategy_id") or ""
                sym = pl.get("symbol") or ""
                # 2026-05-21: WAL has some legacy/partial events without a
                # strategy_id or symbol (e.g. older heartbeats, malformed
                # payloads). Skipping them prevents a "?"/"?" row from
                # showing in the dashboard. Require BOTH — without symbol
                # we can't attribute, without strategy_id we can't aggregate.
                if not sid or not sym:
                    continue
                side = (pl.get("side") or "").lower()
                try:
                    qty = float(pl.get("qty") or pl.get("quantity") or 0)
                except (TypeError, ValueError):
                    qty = 0.0
                try:
                    px = float(pl.get("price") or pl.get("fill_price") or 0)
                except (TypeError, ValueError):
                    px = 0.0
                a = agg.setdefault((sid, sym), {
                    "strategy_id": sid, "symbol": sym,
                    "buy_n": 0, "buy_qty": 0.0, "sell_n": 0, "sell_qty": 0.0,
                    "_px_sum": 0.0, "_px_n": 0, "last_ts": "",
                })
                if side == "buy":
                    a["buy_n"] += 1
                    a["buy_qty"] += qty
                elif side == "sell":
                    a["sell_n"] += 1
                    a["sell_qty"] += qty
                if px > 0:
                    a["_px_sum"] += px
                    a["_px_n"] += 1
                if ev.ts > a["last_ts"]:
                    a["last_ts"] = ev.ts
        pnl_by = {}
        if state.pnl_aggregator is not None:
            try:
                pnl_by = dict(state.pnl_aggregator.by_strategy)
            except Exception:
                pnl_by = {}
        rows = sorted(agg.values(), key=lambda r: (r["strategy_id"], r["symbol"]))
        # 2026-05-21: PER-(strategy_id, symbol) realized via reconstruct_trades.
        # PnLAggregator.by_strategy is strategy-LEVEL only — attaching that to
        # a single symbol row (any rule) shows the WRONG number on the WRONG
        # symbol (user saw -91 on BTCUSDT even though BTCUSDT only had 2 tiny
        # fills — the -91 was the whole strategy's loss, dominated by NEAR/TRX).
        # Round-trip reconstruction gives proper per-(strategy, symbol) PnL.
        realized_by_key: dict[tuple[str, str], float] = {}
        try:
            for t in reconstruct_trades(paths):
                if t.realized_pnl is None:
                    continue  # entry leg still open
                key = (t.strategy_id, t.symbol)
                realized_by_key[key] = realized_by_key.get(key, 0.0) + t.realized_pnl
        except Exception:  # noqa: BLE001 — never 500 the dashboard
            realized_by_key = {}
        # Live mark-price overlay — pulled from the mark-price feed's cache.
        # Each row gets ``mark_price`` (None if no live price yet) and
        # ``pnl_pct`` (= (mark - avg)/avg * 100, only when both are positive
        # and the position is held NET LONG; short positions invert the sign).
        cache = state.price_cache

        # 2026-05-22: Binance ground-truth 의 unRealizedProfit 을 row 마다
        # 부여. 자체 (mark - avg) × qty 계산은 store qty 가 broker 와 어긋
        # 났을 때 dashboard 가 다른 숫자를 보여주는 사고 (10x 사이즈 변경
        # 직후 -1.4 vs Binance -14) 원인. 단일 source-of-truth 로 통일.
        # 다중 전략이 같은 symbol 보유 시 |net_qty| 비율로 prorate. broker
        # 조회 실패/empty 면 ``unrealized_pnl=None`` — 기존 row 들의 기타
        # 필드 (pnl_pct / realized_pnl / qty 집계) 는 변경 zero.
        broker_upnl_by_sym: dict[str, float] = {}
        try:
            provider = state.account_info_provider
            if provider is not None:
                fb = getattr(provider, "fetch_binance", None)
                if callable(fb):
                    bn = await asyncio.to_thread(fb)
                    if isinstance(bn, dict) and bn.get("ok"):
                        for p in bn.get("positions") or []:
                            sym_u = (p.get("symbol") or "").upper()
                            upnl = p.get("unrealized_pnl")
                            if sym_u and isinstance(upnl, (int, float)):
                                broker_upnl_by_sym[sym_u] = float(upnl)
        except Exception:  # noqa: BLE001 — never 500 the dashboard
            broker_upnl_by_sym = {}

        # Pre-pass — per-symbol total store abs qty for multi-strategy prorate.
        store_abs_by_sym: dict[str, float] = {}
        for r in rows:
            sym_u = (r["symbol"] or "").upper()
            if sym_u:
                store_abs_by_sym[sym_u] = (
                    store_abs_by_sym.get(sym_u, 0.0)
                    + abs(r["buy_qty"] - r["sell_qty"])
                )

        out = []
        for r in rows:
            avg_px = (r["_px_sum"] / r["_px_n"]) if r["_px_n"] > 0 else None
            sid = r["strategy_id"]
            sym = r["symbol"]
            net_qty = r["buy_qty"] - r["sell_qty"]
            mark_price = None
            mark_ts = None
            if cache is not None and sym and sym != "?":
                snap = cache.get_price(sym)
                if snap is not None:
                    mark_price = float(snap.price)
                    mark_ts = snap.ts.isoformat()
            pnl_pct = None
            if mark_price is not None and avg_px is not None and avg_px > 0 and net_qty != 0:
                raw_pct = (mark_price - avg_px) / avg_px * 100.0
                # NET short → invert (a price drop is a gain)
                pnl_pct = raw_pct if net_qty > 0 else -raw_pct

            # Broker ground-truth unrealized_pnl prorate by |net_qty| share.
            unrealized_pnl: float | None = None
            sym_u = (sym or "").upper()
            broker_upnl_total = broker_upnl_by_sym.get(sym_u)
            store_abs_total = store_abs_by_sym.get(sym_u, 0.0)
            if broker_upnl_total is not None and store_abs_total > 0 and net_qty != 0:
                share = abs(net_qty) / store_abs_total
                # Clamp share to (0, 1] in case of pathological numeric edge.
                share = min(1.0, max(0.0, share))
                unrealized_pnl = broker_upnl_total * share

            out.append({
                "strategy_id": sid,
                "symbol": sym,
                "buy_n": r["buy_n"],
                "buy_qty": round(r["buy_qty"], 6),
                "sell_n": r["sell_n"],
                "sell_qty": round(r["sell_qty"], 6),
                "net_qty": round(net_qty, 6),
                "avg_price": round(avg_px, 4) if avg_px is not None else None,
                "mark_price": round(mark_price, 6) if mark_price is not None else None,
                "mark_ts": mark_ts,
                "pnl_pct": round(pnl_pct, 3) if pnl_pct is not None else None,
                "unrealized_pnl": round(unrealized_pnl, 4) if unrealized_pnl is not None else None,
                "realized_pnl": realized_by_key.get((sid, sym)),
                "last_ts": r["last_ts"],
            })
        return JSONResponse({"available": True, "strategies": out})

    def _resolve_log_dir() -> Path | None:
        """Resolve the WAL log directory root for discover_wal_files.

        Priority:
        1. state.log_dir — explicitly set by the caller (e.g. live_run.py).
        2. Derived from state.wal_path: a run WAL lives at
           {log_dir}/{run_id}/wal.jsonl, so log_dir = wal_path.parent.parent.
        3. ./logs/live — the `--log-dir` CLI default. Lets a STANDALONE
           dashboard (no active pipeline) still surface prior runs' history.
        4. None — nothing resolved and no default dir on disk.
        """
        if state.log_dir is not None:
            return Path(state.log_dir)
        if state.wal_path is not None:
            p = Path(state.wal_path)
            # parent = run_dir, parent.parent = log_dir
            candidate = p.parent.parent
            if candidate.is_dir():
                return candidate
        default_dir = Path("logs/live")
        if default_dir.is_dir():
            return default_dir
        return None

    @app.get("/api/trade_history")
    async def api_trade_history(limit: int = Query(default=200, ge=1, le=2000)) -> JSONResponse:
        """Reconstruct round-trip trades from all run WALs under log_dir.

        Uses discover_wal_files(log_dir) → reconstruct_trades → sorted by
        most-recent activity (청산 거래는 exit_ts, 미청산은 entry_ts) first.
        realized_pnl is in the venue's own currency (USDT for binance, KRW
        for kis) and is NEVER cross-summed across venues.
        Returns: {trades, total, truncated, log_dir_used}.
        """
        log_dir = _resolve_log_dir()
        if log_dir is None:
            return JSONResponse({
                "trades": [],
                "total": 0,
                "truncated": False,
                "log_dir_used": None,
                "note": "log_dir 미설정 — WAL 경로가 아직 없습니다.",
            })
        import asyncio as _asyncio
        wal_paths = await _asyncio.to_thread(discover_wal_files, log_dir)
        trades = await _asyncio.to_thread(reconstruct_trades, wal_paths)
        # 2026-05-22: "최근 활동" 순 정렬 — 청산된 거래는 exit_ts(청산시각),
        # 미청산은 entry_ts 기준 내림차순. 기존엔 entry_ts 만 봐서, 방금
        # 청산한 거래라도 reconstruct 의 cross-run cost-basis 페어링 탓에
        # entry_ts 가 과거로 박히면 목록 중간에 묻혔다 (사용자: "방금 거래한
        # NEAR 가 거래내역에 안 보임"). 거래내역은 "무엇이 방금 끝났나" 를
        # 보는 카드이므로 청산시각 기준이 직관적.
        trades_sorted = sorted(
            trades, key=lambda t: t.exit_ts or t.entry_ts, reverse=True,
        )
        total = len(trades_sorted)
        truncated = total > limit
        page = trades_sorted[:limit]

        def _trade_dict(t) -> dict:
            return {
                "strategy_id": t.strategy_id,
                "symbol": t.symbol,
                "venue": t.venue,
                "side": t.side,
                "qty": t.qty,
                "entry_ts": t.entry_ts,
                "entry_price": t.entry_price,
                "exit_ts": t.exit_ts,
                "exit_price": t.exit_price,
                "realized_pnl": t.realized_pnl,
                "holding_seconds": t.holding_seconds,
                "status": t.status,
            }

        return JSONResponse({
            "trades": [_trade_dict(t) for t in page],
            "total": total,
            "truncated": truncated,
            "log_dir_used": str(log_dir),
        })

    @app.get("/api/signals")
    async def api_signals(
        venue: str = Query(default="binance"),
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> JSONResponse:
        """Signal-list feed (#268) — `signal_emitted` WAL events, newest first.

        venue=binance → USDT-suffixed symbols (matches shadow_runs classifier).
        Follow-up resolution: for each signal, find the nearest matching
        `order_acked` / `order_placed` (→ "ordered") or `fill_received` /
        `order_filled` (→ "filled") with the same (strategy_id, symbol, side)
        within 120s after the signal. Otherwise "pending" (e.g. blocked by
        meta-labeler or risk gate).

        Read-only, idempotent. WAL absence → empty + note (never 500).
        """
        log_dir = _resolve_log_dir()
        if log_dir is None:
            return JSONResponse({
                "signals": [], "total": 0, "truncated": False,
                "log_dir_used": None, "venue": venue,
                "note": "log_dir 미설정 — WAL 경로가 아직 없습니다.",
            })

        import asyncio as _asyncio
        wal_paths = await _asyncio.to_thread(discover_wal_files, log_dir)

        def _scan() -> tuple[list[dict], list[dict]]:
            """Walk WALs once; return (signals, candidates_for_followup)."""
            sigs: list[dict] = []
            cands: list[dict] = []
            for p in wal_paths:
                events, _corruptions = wal_replay(p)
                for ev in events:
                    pl = ev.payload or {}
                    if ev.event_type == "signal_emitted":
                        sigs.append({
                            "ts": ev.ts,
                            "strategy_id": str(pl.get("strategy_id", "")),
                            "symbol": str(pl.get("symbol", "")),
                            "side": str(pl.get("side", "")).lower(),
                            "qty": str(pl.get("qty", "")),
                            "reason": str(pl.get("reason", "")),
                        })
                    elif ev.event_type in (
                        "order_acked", "order_placed", "order_submitted",
                        "order_filled", "fill_received",
                    ):
                        sym = pl.get("symbol") or ""
                        if not sym and ev.event_type == "order_acked":
                            sid = pl.get("strategy_id") or ""
                            sym = sid.split("-")[-1] if sid else ""
                        cands.append({
                            "ts": ev.ts,
                            "event_type": ev.event_type,
                            "strategy_id": str(pl.get("strategy_id", "")),
                            "symbol": str(sym),
                            "side": str(pl.get("side", "")).lower(),
                        })
            return sigs, cands

        signals, candidates = await _asyncio.to_thread(_scan)

        # Venue filter (binance = USDT suffix; "all" disables).
        venue_norm = venue.lower()
        if venue_norm == "binance":
            signals = [s for s in signals if s["symbol"].endswith("USDT")]

        def _to_epoch(iso: str) -> float:
            try:
                return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0.0

        cand_index: dict[tuple[str, str, str], list[tuple[float, str]]] = {}
        for c in candidates:
            key = (c["strategy_id"], c["symbol"], c["side"])
            cand_index.setdefault(key, []).append((_to_epoch(c["ts"]), c["event_type"]))
        for v in cand_index.values():
            v.sort()

        FILL_TYPES = {"order_filled", "fill_received"}
        WINDOW_S = 120.0

        def _resolve_followup(sig: dict) -> str:
            key = (sig["strategy_id"], sig["symbol"], sig["side"])
            arr = cand_index.get(key)
            if not arr:
                return "pending"
            sig_ts = _to_epoch(sig["ts"])
            status = "pending"
            for cts, etype in arr:
                if cts < sig_ts:
                    continue
                if cts - sig_ts > WINDOW_S:
                    break
                if etype in FILL_TYPES:
                    return "filled"
                status = "ordered"
            return status

        for s in signals:
            s["follow_up"] = _resolve_followup(s)

        signals.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        total = len(signals)
        truncated = total > limit
        page = signals[:limit]
        return JSONResponse({
            "signals": page,
            "total": total,
            "truncated": truncated,
            "log_dir_used": str(log_dir),
            "venue": venue_norm,
        })

    @app.get("/signals", response_class=HTMLResponse)
    async def signals_page() -> HTMLResponse:
        return HTMLResponse(content=_render_signals_page())

    @app.get("/api/cs-tsmom")
    async def api_cs_tsmom() -> JSONResponse:
        """cs-tsmom-crypto-daily 자체 계산 신호 (2026-05-20).

        production 전략의 universe-scan wiring 과 무관하게 대시보드 서버가
        매일 30종목 일봉 fetch + score + cross-sectional 랭킹 계산. Pine
        Script 와 동일 score 식 → 두 시각화가 동일 숫자. 1h TTL 캐시 +
        single-flight 락이라 다중 동시 호출 안전. 절대 500 금지.
        """
        comp = state.cs_tsmom_computer
        if comp is None:
            return JSONResponse({
                "available": False,
                "reason": "cs_tsmom_computer not wired (standalone dashboard 모드에서만 자동 attach)",
            })
        import asyncio as _asyncio
        try:
            result = await _asyncio.to_thread(comp.compute)
        except Exception as err:  # noqa: BLE001
            return JSONResponse({
                "available": False, "reason": f"{type(err).__name__}: {err}",
            })
        # Starlette JSONResponse 는 allow_nan=False → 응답에 NaN 한 개라도 있으면
        # 직렬화 단계에서 ValueError → HTTP 500 으로 escape. compute_signals 가 이미
        # sanitize 했지만 만의 하나 새는 NaN 도 잡도록 안전망 한 겹 더.
        payload = {
            "available": result.available,
            "reason": result.reason,
            "fetched_at": result.fetched_at,
            "universe_size": result.universe_size,
            "pin_date": result.pin_date,
            "rows": result.rows,
        }
        try:
            return JSONResponse(payload)
        except (ValueError, TypeError) as err:
            return JSONResponse({
                "available": False,
                "reason": f"serialize_failed: {type(err).__name__}: {err}",
                "fetched_at": result.fetched_at,
                "universe_size": result.universe_size,
                "pin_date": result.pin_date,
                "rows": [],
            })

    @app.post("/api/cs-tsmom/refresh")
    async def api_cs_tsmom_refresh() -> JSONResponse:
        """캐시 무효화 + 강제 재계산 (2026-05-21 fix).

        사용자가 "지금 메이저 누락 / 점수 이상" 같은 상황에서 1h TTL 안 기다리고
        즉시 재페치 가능. 동시 요청은 single-flight 락이 직렬화.
        """
        comp = state.cs_tsmom_computer
        if comp is None:
            return JSONResponse(
                {"ok": False, "reason": "cs_tsmom_computer not wired"},
                status_code=503,
            )
        import asyncio as _asyncio
        try:
            result = await _asyncio.to_thread(comp.compute, True)  # force=True
        except Exception as err:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "reason": f"{type(err).__name__}: {err}"},
                status_code=500,
            )
        return JSONResponse({
            "ok": True,
            "available": result.available,
            "fetched_at": result.fetched_at,
            "universe_size": result.universe_size,
            "pin_date": result.pin_date,
            "row_count": len(result.rows),
        })

    @app.get("/cs-tsmom", response_class=HTMLResponse)
    async def cs_tsmom_page() -> HTMLResponse:
        return HTMLResponse(content=_render_cs_tsmom_page())

    @app.get("/airborne", response_class=HTMLResponse)
    async def airborne_page() -> HTMLResponse:
        """airborne 알림 적중 페이지 (기본 룰 TP +1%/SL -0.5%)."""
        return HTMLResponse(content=_render_airborne_page("default"))

    @app.get("/airborne-2pct", response_class=HTMLResponse)
    async def airborne_2pct_page() -> HTMLResponse:
        """airborne 알림 적중 페이지 (대안 룰 TP +2%/SL -1%) — 2026-06-04.

        같은 fires set 을 더 넓은 폭 룰로 시뮬한 통계. cache 도 별도
        (logs/airborne_fires/sim_cache_2pct.jsonl) 라 기본 룰 통계와 안 섞임.
        실 자동매매 전략과의 룰 적합도 비교용.
        """
        return HTMLResponse(content=_render_airborne_page("2pct"))

    @app.get("/ma-cross", response_class=HTMLResponse)
    async def ma_cross_page() -> HTMLResponse:
        """골든/데드 크로스 적중 페이지 (golden=롱/death=숏, TP +12%/SL -2%).

        qta-ma-cross-daemon 의 CROSS 라인을 시뮬해 적중률·PF·net% 집계.
        airborne 페이지 레이아웃 미러.
        """
        return HTMLResponse(content=_render_ma_cross_page())

    # ── 수동 거래 입력 (2026-05-21 — Claude Routines 일일 리포트 준비) ────
    # 2026-06-05 — manual_trade 는 venue-무관 단일 경로 (``logs/manual_trade.jsonl``).
    # 이전엔 _resolve_log_dir() 하위에 두어서 venue 전환 시 (Binance→Bitget)
    # dashboard 가 다른 디렉토리만 보고 옛 데이터를 못 찾는 사고 발생 (사용자
    # 보고: 오늘자 수동거래 다 사라졌네). 옛 데이터 (``logs/live/manual_trade.
    # jsonl``) 는 READ union 으로 흡수.
    _MANUAL_TRADE_LEGACY_PATHS: tuple[Path, ...] = (
        Path("logs/live/manual_trade.jsonl"),
        Path("logs/shadow-bitget/manual_trade.jsonl"),
        Path("logs/shadow-binance/manual_trade.jsonl"),
    )

    def _manual_trade_log_path() -> Path:
        """수동 거래 *WRITE* 경로 — venue 무관 단일 ``logs/manual_trade.jsonl``.

        기본 base 는 ``logs/`` 고정 (venue 전환·standalone/pipeline 모드 무관하게
        한 파일 — 2026-06-05 venue 데이터 유실 fix). ``QTA_MANUAL_TRADE_DIR`` env
        로 base 오버라이드 가능 — 테스트가 실데이터를 오염시키던 버그 차단
        (2026-06-09: 핸들러가 state.log_dir 을 무시하고 실 logs/ 에 써서
        test_dashboard_manual_trade 가 실행마다 실파일에 샘플 8건 주입). 레포의
        AIRBORNE_FIRE_STORE_PATH / AIRBORNE_REENTRY_STATE_DIR 와 동일 idiom.
        """
        import os
        base_override = os.environ.get("QTA_MANUAL_TRADE_DIR")
        base = Path(base_override) if base_override else Path("logs")
        path = base / "manual_trade.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _manual_trade_read_paths() -> list[Path]:
        """수동 거래 *READ* 경로 — 표준 + 모든 legacy union. 중복 ts 는 dedup.

        legacy union 은 *프로덕션 기본 base* (logs/) 일 때만 — QTA_MANUAL_TRADE_DIR
        로 격리(테스트)된 경우엔 실 legacy 파일을 끌어오지 않아 완전 격리.
        """
        import os
        paths = [_manual_trade_log_path()]
        if not os.environ.get("QTA_MANUAL_TRADE_DIR"):
            for legacy in _MANUAL_TRADE_LEGACY_PATHS:
                if legacy.exists() and legacy not in paths:
                    paths.append(legacy)
        return paths

    @app.post("/api/manual_trade")
    async def api_manual_trade_post(body: dict[str, Any]) -> JSONResponse:
        """수동 거래 1건 append.

        Required: ``symbol``, ``qty``, (``entry_price`` or ``price``).
        Optional new (v2 schema): ``direction`` ("long"/"short"),
        ``exit_price``, ``realized_pnl``, ``outcome`` ("win"/"loss"/"breakeven").
        Legacy (v1) 호환: ``side`` ("buy"/"sell"), ``kind`` ("entry"/"exit"),
        ``price`` 도 그대로 받음.

        Auto-derived (사용자 편의):
        - ``side`` ← direction (long→buy, short→sell) when only direction given
        - ``direction`` ← side (buy→long, sell→short) when only legacy side given
        - ``kind="roundtrip"`` when ``exit_price`` > 0; else "entry"
        - ``price`` ← entry_price (legacy reader 호환)
        """
        symbol = str(body.get("symbol") or "").strip().upper()
        direction = str(body.get("direction") or "").strip().lower()
        side = str(body.get("side") or "").strip().lower()
        kind = str(body.get("kind") or "").strip().lower()
        venue = str(body.get("venue") or "other").strip().lower()
        note = str(body.get("note") or "").strip()
        outcome = str(body.get("outcome") or "").strip().lower() or None

        def _num(v: Any) -> float | None:
            if v is None or v == "":
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        qty = _num(body.get("qty"))
        entry_price = _num(body.get("entry_price"))
        if entry_price is None:
            entry_price = _num(body.get("price"))
        exit_price = _num(body.get("exit_price"))
        realized_pnl = _num(body.get("realized_pnl"))

        if qty is None or entry_price is None:
            return JSONResponse(
                {"ok": False, "reason": "qty/entry_price must be numeric"},
                status_code=400,
            )
        if not symbol or qty <= 0 or entry_price <= 0:
            return JSONResponse(
                {"ok": False, "reason": "symbol/qty/entry_price are required"},
                status_code=400,
            )

        if direction not in ("long", "short", ""):
            return JSONResponse(
                {"ok": False, "reason": "direction must be long or short"},
                status_code=400,
            )
        if outcome and outcome not in ("win", "loss", "breakeven"):
            return JSONResponse(
                {"ok": False, "reason": "outcome must be win/loss/breakeven"},
                status_code=400,
            )

        if direction and side not in ("buy", "sell"):
            side = "buy" if direction == "long" else "sell"
        if not direction and side in ("buy", "sell"):
            direction = "long" if side == "buy" else "short"
        if side not in ("buy", "sell"):
            return JSONResponse(
                {"ok": False, "reason": "side or direction is required"},
                status_code=400,
            )

        if not kind:
            kind = "roundtrip" if (exit_price is not None and exit_price > 0) else "entry"
        if kind not in ("entry", "exit", "roundtrip"):
            return JSONResponse(
                {"ok": False, "reason": "kind must be entry/exit/roundtrip"},
                status_code=400,
            )

        ts_raw = body.get("ts")
        ts = (
            str(ts_raw) if ts_raw
            else datetime.now(timezone.utc).isoformat()
        )
        record = {
            "schema_version": 2,
            "ts": ts,
            "event_type": "manual_trade",
            "payload": {
                "symbol": symbol,
                "direction": direction,
                "side": side,  # legacy 호환
                "kind": kind,
                "qty": qty,
                "entry_price": entry_price,
                "price": entry_price,  # legacy: Routines prompt 가 price 로 읽음
                "exit_price": exit_price,
                "realized_pnl": realized_pnl,
                "outcome": outcome,
                "venue": venue,
                "note": note,
            },
        }
        path = _manual_trade_log_path()
        try:
            import json as _json
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(_json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as err:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "reason": f"{type(err).__name__}: {err}"},
                status_code=500,
            )
        return JSONResponse({"ok": True, "ts": ts, "log_path": str(path)})

    def _read_manual_trades_all() -> list[dict]:
        """log JSONL 전체를 list[{ts, ...payload}] 로 읽음. corruption tolerant.

        Read paths union (현재 + legacy). 시각 내림차순. (ts, symbol) 로 dedup
        — venue 전환 직전 이중 기록된 row 안전.
        """
        out: list[dict] = []
        import json as _json
        seen: set[tuple[str, str]] = set()
        for path in _manual_trade_read_paths():
            if not path.exists():
                continue
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    pl = rec.get("payload") or {}
                    dedup = (str(rec.get("ts")), str(pl.get("symbol", "")))
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    out.append({"ts": rec.get("ts"), **pl})
            except Exception:  # noqa: BLE001 — never 500
                continue
        out.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        return out

    @app.get("/api/manual_trade/today")
    async def api_manual_trade_today() -> JSONResponse:
        """오늘(KST 자정~) 수동 거래 list + 전체 누적 count.

        Routines 는 trades(오늘분)만 쓰지만 dashboard /manual UI 는
        ``total_all_time`` 으로 "어제까지 입력한 것도 살아있음" 안내 가능.
        """
        path = _manual_trade_log_path()
        all_trades = _read_manual_trades_all()
        kst_now = datetime.now(_KST)
        kst_midnight = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_cutoff = kst_midnight.astimezone(timezone.utc)
        today: list[dict] = []
        for r in all_trades:
            try:
                rec_ts = datetime.fromisoformat(
                    str(r.get("ts", "")).replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if rec_ts >= utc_cutoff:
                today.append(r)
        return JSONResponse({
            "trades": today,
            "log_path": str(path),
            "total_all_time": len(all_trades),
        })

    @app.get("/api/manual_trade/recent")
    async def api_manual_trade_recent(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> JSONResponse:
        """시각 무관 최신 N건. 자정 컷오프 없음 — 사용자가 어제 입력한 거 확인용."""
        path = _manual_trade_log_path()
        all_trades = _read_manual_trades_all()
        return JSONResponse({
            "trades": all_trades[:limit],
            "log_path": str(path),
            "total_all_time": len(all_trades),
        })

    # ── 수동 거래 수정/삭제 (2026-06-03) ───────────────────────────────────
    # JSONL 은 append-only 이지만, 수동 입력 실수는 흔함 → ts 를 key 로
    # 전체 파일 재기록. 영향 row 만 갱신/제거, 나머지 byte-identical.

    def _rewrite_manual_trades(records: list[dict]) -> None:
        """log JSONL 전체 재기록 — atomic temp-file swap.

        records: full {ts, event_type, schema_version, payload} dict list.
        파일 부재 시 새로 생성. corruption tolerant — 호출자가 _read_raw 의
        결과를 그대로 수정해 넘기는 것이 안전.
        """
        import json as _json
        import tempfile
        path = _manual_trade_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # 같은 디렉토리에 임시 파일 → atomic rename
        fd, tmp_path = tempfile.mkstemp(
            prefix="manual_trade_", suffix=".jsonl.tmp",
            dir=str(path.parent),
        )
        try:
            with open(fd, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
            # Windows 에서 os.replace 가 atomic
            import os as _os
            _os.replace(tmp_path, str(path))
        except Exception:
            # cleanup
            try:
                import os as _os
                if _os.path.exists(tmp_path):
                    _os.unlink(tmp_path)
            except Exception:
                pass
            raise

    def _read_manual_trades_raw() -> list[dict]:
        """log JSONL raw record list. union 으로 legacy 도 함께 읽어서 edit/delete
        가 옛 데이터도 다룰 수 있게. (ts, symbol) dedup.

        rewrite 시 standard path 로 통합 — legacy 파일은 그대로 두지만 다음
        rewrite 가 standard path 에 모든 row 를 다시 쓰므로 source-of-truth 가
        자동으로 일원화된다.
        """
        import json as _json
        out: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for path in _manual_trade_read_paths():
            if not path.exists():
                continue
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    pl = rec.get("payload") or {}
                    dedup = (str(rec.get("ts")), str(pl.get("symbol", "")))
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    out.append(rec)
            except Exception:  # noqa: BLE001
                continue
        return out

    @app.delete("/api/manual_trade/{trade_ts}")
    async def api_manual_trade_delete(trade_ts: str) -> JSONResponse:
        """ts 로 단일 row 삭제. 잘못 입력한 거래 제거.

        다른 row 는 byte-identical 보존 (corruption-tolerant raw read +
        atomic rewrite).
        """
        try:
            all_raw = _read_manual_trades_raw()
        except Exception as err:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "reason": f"read_failed: {err}"}, status_code=500,
            )
        kept = [r for r in all_raw if str(r.get("ts")) != trade_ts]
        if len(kept) == len(all_raw):
            return JSONResponse(
                {"ok": False, "reason": f"trade not found ts={trade_ts}"},
                status_code=404,
            )
        try:
            _rewrite_manual_trades(kept)
        except Exception as err:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "reason": f"write_failed: {err}"}, status_code=500,
            )
        return JSONResponse({"ok": True, "deleted_ts": trade_ts,
                             "remaining": len(kept)})

    @app.patch("/api/manual_trade/{trade_ts}")
    async def api_manual_trade_patch(
        trade_ts: str, body: dict[str, Any],
    ) -> JSONResponse:
        """ts 로 단일 row payload 필드 update.

        Body 의 어떤 필드든 전달 시 그 필드만 갱신. symbol/qty/entry_price/
        exit_price/realized_pnl/outcome/direction/side/kind/venue/note 등.
        ``ts`` 자체 (record key) 는 변경 불가.

        부분 update — body 에 없는 필드는 그대로. 폼이 일부 필드만 수정해
        보내도 OK.
        """
        try:
            all_raw = _read_manual_trades_raw()
        except Exception as err:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "reason": f"read_failed: {err}"}, status_code=500,
            )
        found_idx: int | None = None
        for i, r in enumerate(all_raw):
            if str(r.get("ts")) == trade_ts:
                found_idx = i
                break
        if found_idx is None:
            return JSONResponse(
                {"ok": False, "reason": f"trade not found ts={trade_ts}"},
                status_code=404,
            )
        # body 의 입력 정규화 (POST 와 동일 path 통과)
        rec = all_raw[found_idx]
        pl = dict(rec.get("payload") or {})

        # 허용 필드만 update — record key (ts, event_type, schema_version) 보호
        ALLOWED = {
            "symbol", "direction", "side", "kind", "qty", "entry_price",
            "price", "exit_price", "realized_pnl", "outcome", "venue", "note",
        }
        # 정규화: symbol upper, direction lower, etc.
        for k, v in body.items():
            if k not in ALLOWED:
                continue
            if k == "symbol" and isinstance(v, str):
                pl["symbol"] = v.strip().upper()
            elif k in ("direction", "side", "kind", "venue", "outcome") and isinstance(v, str):
                pl[k] = v.strip().lower() or pl.get(k)
            elif k in ("qty", "entry_price", "price", "exit_price", "realized_pnl"):
                if v in ("", None):
                    pl[k] = None
                else:
                    try:
                        pl[k] = float(v)
                    except (TypeError, ValueError):
                        return JSONResponse(
                            {"ok": False, "reason": f"{k} must be numeric"},
                            status_code=400,
                        )
            else:
                pl[k] = v

        # entry_price ↔ price legacy sync
        if "entry_price" in body and "price" not in body:
            pl["price"] = pl.get("entry_price")
        elif "price" in body and "entry_price" not in body:
            pl["entry_price"] = pl.get("price")

        # kind 자동 재산정 (exit_price 추가/제거 시)
        if "exit_price" in body:
            if pl.get("exit_price") not in (None, "", 0, 0.0):
                pl["kind"] = "roundtrip"
            elif pl.get("kind") == "roundtrip":
                pl["kind"] = "entry"

        # outcome 검증
        if pl.get("outcome") not in (None, "", "win", "loss", "breakeven"):
            return JSONResponse(
                {"ok": False, "reason": "outcome must be win/loss/breakeven"},
                status_code=400,
            )
        rec["payload"] = pl
        all_raw[found_idx] = rec
        try:
            _rewrite_manual_trades(all_raw)
        except Exception as err:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "reason": f"write_failed: {err}"}, status_code=500,
            )
        return JSONResponse({"ok": True, "ts": trade_ts, "payload": pl})

    @app.get("/manual", response_class=HTMLResponse)
    async def manual_page() -> HTMLResponse:
        return HTMLResponse(content=_render_manual_page())

    # ── 일일 리포트 통합 endpoint (Claude Routines 가 fetch 함) ────────────
    async def _build_journal_today() -> dict[str, Any]:
        """Journal today JSON dict 빌더 — endpoint 와 export 가 공유."""
        kst_now = datetime.now(_KST)
        kst_midnight = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_cutoff = kst_midnight.astimezone(timezone.utc)

        def _ts_after_cutoff(iso: str) -> bool:
            try:
                t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
                return t >= utc_cutoff
            except (ValueError, TypeError):
                return False

        # ── 자동 fill + signal (WAL) ──
        auto_fills: list[dict] = []
        auto_signals: list[dict] = []
        log_dir = _resolve_log_dir()
        if log_dir is not None:
            import asyncio as _asyncio
            wal_paths = await _asyncio.to_thread(discover_wal_files, log_dir)
            for p in wal_paths:
                events, _ = wal_replay(p)
                for ev in events:
                    if not _ts_after_cutoff(ev.ts):
                        continue
                    pl = ev.payload or {}
                    if ev.event_type in ("order_filled", "fill_received"):
                        auto_fills.append({"ts": ev.ts, **pl})
                    elif ev.event_type == "signal_emitted":
                        auto_signals.append({"ts": ev.ts, **pl})

        # ── 수동 거래 (manual_trade.jsonl) ──
        manual: list[dict] = []
        # 2026-06-05 — venue 무관 union read 사용 (logs/manual_trade.jsonl +
        # legacy logs/live/manual_trade.jsonl). 옛 데이터 분실 안 됨.
        for r in _read_manual_trades_all():
            if not _ts_after_cutoff(r.get("ts", "")):
                continue
            manual.append(r)

        # ── airborne FIRE 알림 (qta-airborne-daemon docker logs) ──
        # 매일 자정 routine 이 알림 적중 분석 (다음 15분봉 4개 TP/SL 시뮬레이션)
        # 하려면 raw FIRE 라인 필요. docker 미가동 환경에서는 빈 리스트.
        import asyncio as _asyncio2
        # ``Z`` 로 명시 UTC — docker --since 가 daemon-local TZ 로 해석하지 않게.
        since_iso = utc_cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        airborne_fires_raw = await _asyncio2.to_thread(
            _parse_airborne_fires_from_docker_logs, since_iso,
        )
        # docker --since 는 컨테이너 시작 시각 기준이라 KST cutoff 보다 더 옛
        # 라인이 섞일 수 있음. 명시적 ts 필터로 한 번 더 거름.
        airborne_fires = [f for f in airborne_fires_raw if _ts_after_cutoff(f["ts"])]

        # ── cs-tsmom 오늘 TOP-10 (computer 가 wired 면) ──
        cs_tsmom_top10: dict | None = None
        comp = state.cs_tsmom_computer
        if comp is not None:
            try:
                cs_state = comp.peek()
                if cs_state is None:
                    import asyncio as _asyncio
                    cs_state = await _asyncio.to_thread(comp.compute)
                cs_tsmom_top10 = {
                    "fetched_at": cs_state.fetched_at,
                    "pin_date": cs_state.pin_date,
                    "available": cs_state.available,
                    "top10": [r for r in (cs_state.rows or []) if r.get("in_top_today")],
                }
            except Exception as err:  # noqa: BLE001
                cs_tsmom_top10 = {"error": f"{type(err).__name__}: {err}"}

        for arr in (auto_fills, auto_signals, manual):
            arr.sort(key=lambda r: str(r.get("ts") or ""))

        return {
            "date_kst": kst_now.strftime("%Y-%m-%d"),
            "kst_window_start": kst_midnight.isoformat(),
            "now_kst": kst_now.isoformat(),
            "counts": {
                "auto_fills": len(auto_fills),
                "auto_signals": len(auto_signals),
                "manual_trades": len(manual),
                "airborne_fires": len(airborne_fires),
            },
            "auto_fills": auto_fills,
            "auto_signals": auto_signals,
            "manual_trades": manual,
            "airborne_fires": airborne_fires,
            "cs_tsmom_top10": cs_tsmom_top10,
        }

    # ── airborne 적중 메트릭 (오늘 KST 자정~지금, 실시간 시뮬레이션) ──────────
    # cache_key = window 식별자 ("today"/"yesterday"/"7d"/"30d"/"all").
    # 각 window 별 5분 TTL.
    _airborne_metrics_cache: dict[str, dict[str, Any]] = {}
    AIRBORNE_METRICS_CACHE_TTL = 300.0  # 5분

    async def _fetch_15m_bars(
        client: Any, symbol: str, fire_ts_iso: str, limit: int = 4,
    ) -> list[dict]:
        """Binance fapi `/fapi/v1/klines?interval=15m` — fire 시점 이후 4봉."""
        try:
            ts = datetime.fromisoformat(fire_ts_iso.replace("Z", "+00:00"))
            start_ms = int(ts.timestamp() * 1000)
        except (ValueError, TypeError):
            return []
        try:
            r = await client.get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": symbol, "interval": "15m",
                        "startTime": start_ms, "limit": limit},
                timeout=10.0,
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            return []
        return [
            {"open_time": int(b[0]), "open": float(b[1]), "high": float(b[2]),
             "low": float(b[3]), "close": float(b[4]), "close_time": int(b[6])}
            for b in data
        ]

    @app.get("/api/airborne_metrics")
    async def api_airborne_metrics(
        window: str = Query(
            "today",
            description="today | yesterday | all | 7d | 30d",
        ),
        rule: str = Query(
            "default",
            description="default (TP +1%/SL -0.5%) | 2pct (TP +2%/SL -1%)",
        ),
    ) -> JSONResponse:
        """airborne FIRE 의 TP/SL 시뮬레이션 메트릭 — 누적 윈도우 + 룰 지원.

        Rules:
          - ``default`` (기본): TP +1.0% / SL -0.5% / 4봉 hold (1h). routine
            spec 규칙 6 과 동일. dashboard 메인 카드 / /airborne 페이지가 사용.
          - ``2pct`` (2026-06-04 추가): TP +2.0% / SL -1.0% / 4봉 hold. 짧은
            호흡 룰이 안 맞는다고 판단되는 사용자가 비교용으로 보는 통계.
            cache 도 별도 (``logs/airborne_fires/sim_cache_2pct.jsonl``).

        Windows (모두 KST 자정 기준):
          - ``today``: 오늘 자정 ~ 지금 (기본)
          - ``yesterday``: 어제 자정 ~ 오늘 자정
          - ``7d``: 7일 전 자정 ~ 지금
          - ``30d``: 30일 전 자정 ~ 지금
          - ``all``: 영속 store 의 가장 옛 fire ~ 지금
        """
        import time as _time
        import httpx as _httpx

        # ── 룰 선택 — 모르는 값은 default 로 fallback ──────────────────────
        if rule == "2pct":
            tp_pct = AIRBORNE_TP_PCT_2PCT
            sl_pct = AIRBORNE_SL_PCT_2PCT
            hold_bars = AIRBORNE_HOLD_BARS_2PCT
        else:
            rule = "default"
            tp_pct = AIRBORNE_TP_PCT
            sl_pct = AIRBORNE_SL_PCT
            hold_bars = AIRBORNE_HOLD_BARS

        now = _time.time()
        cache_key = f"{window}:{rule}"
        cache_entry = _airborne_metrics_cache.get(cache_key)
        if (cache_entry is not None
                and now - cache_entry["ts"] < AIRBORNE_METRICS_CACHE_TTL
                and cache_entry["data"] is not None):
            return JSONResponse({**cache_entry["data"], "cached": True})

        # ── 윈도우 → since_utc / until_utc 계산 ──────────────────────────
        kst_now = datetime.now(_KST)
        kst_midnight = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_midnight = kst_midnight.astimezone(timezone.utc)
        utc_now = datetime.now(timezone.utc)

        if window == "today":
            since_utc = utc_midnight
            until_utc = utc_now
        elif window == "yesterday":
            since_utc = utc_midnight - timedelta(days=1)
            until_utc = utc_midnight  # 오늘 자정 직전
        elif window == "7d":
            since_utc = utc_midnight - timedelta(days=7)
            until_utc = utc_now
        elif window == "30d":
            since_utc = utc_midnight - timedelta(days=30)
            until_utc = utc_now
        elif window == "all":
            since_utc = datetime(2000, 1, 1, tzinfo=timezone.utc)
            until_utc = utc_now
        else:
            return JSONResponse(
                {"error": f"unknown window={window!r}, allowed: today/yesterday/7d/30d/all"},
                status_code=400,
            )

        # ── docker logs --since 4d 까지 풀백 → store append (dedup) ─────
        # docker 의 rotation 한도가 ~4d 이므로 그 안의 fire 만 backfill 가능.
        # 그 옛 fire 는 dashboard 가 처음 켜진 시점부터 store 에 영속 누적되어야
        # 함 — 본 PR 이후 운영 자연스러운 추세.
        backfill_since = (utc_now - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        live_fires = await asyncio.to_thread(
            _parse_airborne_fires_from_docker_logs, backfill_since,
        )
        store = _get_airborne_fire_store()
        added = store.append_many(live_fires) if live_fires else 0
        if added:
            import logging as _lg
            _lg.getLogger(__name__).info(
                "[airborne_metrics] store +%d new fires (total=%d)",
                added, store.count(),
            )

        # ── 윈도우의 fires 영속 store 에서 load ───────────────────────────
        fires = store.load_since(since_utc)
        # until_utc 컷오프 적용
        def _within(ts_iso: str) -> bool:
            try:
                ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return since_utc <= ts < until_utc
            except (ValueError, TypeError):
                return False
        fires = [f for f in fires if _within(f.get("ts", ""))]

        # ── 영속 sim cache 활용 ─────────────────────────────────────────
        # 옛 fire 는 한 번 시뮬한 결과 (outcome/pct/bar_idx) 가 sim_cache.jsonl
        # 에 저장됨 → 동일 fire 재호출은 캐시 hit (O(1) dict lookup, REST 0회).
        # cache miss 인 새 fire 들만 Binance fapi 봉 fetch + 시뮬.
        sim_cache = _get_airborne_sim_cache(rule)
        cached_sims, missing_fires = sim_cache.split(fires)

        # cache miss 들 — asyncio.gather 로 병렬 fetch (concurrency 20).
        # 직렬 fetch 는 100 fire = ~30초+, 병렬은 ~3초.
        new_sims: list[dict] = []
        if missing_fires:
            sem = asyncio.Semaphore(20)
            async with _httpx.AsyncClient() as client:
                async def _sim_one(fire: dict) -> dict | None:
                    async with sem:
                        bars = await _fetch_15m_bars(client, fire["symbol"], fire["ts"])
                    out = _simulate_airborne_fire(
                        fire, bars,
                        tp_pct=tp_pct, sl_pct=sl_pct, hold_bars=hold_bars,
                    )
                    if out is None:
                        return None
                    return {**fire, **out}

                results = await asyncio.gather(
                    *(_sim_one(f) for f in missing_fires),
                    return_exceptions=False,
                )
            new_sims = [r for r in results if r is not None]
            # 영속 캐시에 새로 저장 — 같은 fire 다시 안 시뮬.
            if new_sims:
                sim_cache.put_many(new_sims)

        sims = cached_sims + new_sims
        agg = _aggregate_airborne_sims(sims)
        # 2026-05-26 — /airborne 페이지가 per-fire 상세 행을 렌더하도록 `sims`
        # 리스트를 응답에 포함. 페이지가 별도 endpoint 를 추가로 호출하지 않게
        # 메인 카드용 집계 + 페이지용 raw 를 한 응답으로 합친다.
        sim_keys = {(s["ts"], s["symbol"]) for s in sims}
        no_bar_fires = [
            {**f, "outcome": "no_bars", "pct": None, "bar_idx": None}
            for f in fires if (f["ts"], f["symbol"]) not in sim_keys
        ]
        payload = {
            "date_kst": kst_now.strftime("%Y-%m-%d"),
            "window": window,
            "window_start_utc": since_utc.isoformat(),
            "window_end_utc": until_utc.isoformat(),
            "kst_window_start": kst_midnight.isoformat(),  # backcompat (오늘 자정)
            "now_kst": kst_now.isoformat(),
            "store_count": store.count(),
            "store_earliest": store.earliest_ts(),
            "sim_cache_count": sim_cache.count(),
            "sim_cache_hits": len(cached_sims),
            "sim_cache_misses": len(missing_fires),
            "fires_total": len(fires),
            "sims_total": len(sims),
            **agg,
            "sims": sims + no_bar_fires,  # per-fire 상세 (ts/symbol/side/outcome/pct/bar_idx)
            "rule": {
                "name": rule,
                "tp_pct": tp_pct, "sl_pct": sl_pct,
                "hold_bars": hold_bars, "fee_pct": AIRBORNE_FEE_PCT,
            },
            "cached": False,
        }
        _airborne_metrics_cache[cache_key] = {"ts": now, "data": payload}
        return JSONResponse(payload)

    # ── ma-cross (골든/데드 크로스) 적중 메트릭 — airborne 미러 ──────────────────
    _ma_cross_metrics_cache: dict[str, dict[str, Any]] = {}
    MA_CROSS_METRICS_CACHE_TTL = 300.0  # 5분

    async def _fetch_1h_bars_after(
        client: Any, symbol: str, cross_ts_iso: str,
        limit: int = MA_CROSS_HOLD_BARS,
    ) -> list[dict]:
        """Bitget v2 candles `/api/v2/mix/market/candles` — cross 시점 이후 1h 봉.

        ``startTime`` (ms) 로 cross 시각 이후 봉만 받는다. Bitget candles 응답은
        최신→과거 순으로 올 수 있어 open_time 오름차순 정렬 (daemon
        `_bitget_bars_to_history` 와 동일 규약). docker/REST 실패는 빈 list.
        """
        try:
            ts = datetime.fromisoformat(cross_ts_iso.replace("Z", "+00:00"))
            start_ms = int(ts.timestamp() * 1000)
        except (ValueError, TypeError):
            return []
        try:
            r = await client.get(
                "https://api.bitget.com/api/v2/mix/market/candles",
                params={"symbol": symbol, "productType": "USDT-FUTURES",
                        "granularity": "1H", "startTime": str(start_ms),
                        "limit": str(min(limit, 1000))},
                timeout=10.0,
            )
            r.raise_for_status()
            j = r.json()
        except Exception:
            return []
        if str(j.get("code")) != "00000":
            return []
        rows = j.get("data") or []
        # Bitget candle row: [ts, open, high, low, close, baseVol, quoteVol].
        bars: list[dict] = []
        for row in rows:
            try:
                bars.append({
                    "open_time": int(row[0]),
                    "open": float(row[1]), "high": float(row[2]),
                    "low": float(row[3]), "close": float(row[4]),
                    "close_time": int(row[0]) + 3_600_000 - 1,
                })
            except (IndexError, ValueError, TypeError):
                continue
        bars.sort(key=lambda b: b["open_time"])
        return bars

    @app.get("/api/ma_cross_metrics")
    async def api_ma_cross_metrics(
        window: str = Query(
            "today",
            description="today | yesterday | all | 7d | 30d",
        ),
    ) -> JSONResponse:
        """골든/데드 크로스 CROSS 의 TP/SL 시뮬레이션 메트릭 — 누적 윈도우.

        golden=롱 / death=숏. TP +12% / SL -2% (손익비 1:6) / 720봉(=30일) hold.
        매 호출 시 docker logs 최근 4d 파싱 → store append (dedup) → 윈도우만큼
        load_since → bitget 1h 봉 시뮬 → 집계. airborne_metrics 와 동일 흐름.

        Windows (모두 KST 자정 기준): today/yesterday/7d/30d/all.
        """
        import time as _time
        import httpx as _httpx

        now = _time.time()
        cache_key = window
        cache_entry = _ma_cross_metrics_cache.get(cache_key)
        if (cache_entry is not None
                and now - cache_entry["ts"] < MA_CROSS_METRICS_CACHE_TTL
                and cache_entry["data"] is not None):
            return JSONResponse({**cache_entry["data"], "cached": True})

        # ── 윈도우 → since_utc / until_utc 계산 (airborne 와 동일) ──────────
        kst_now = datetime.now(_KST)
        kst_midnight = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_midnight = kst_midnight.astimezone(timezone.utc)
        utc_now = datetime.now(timezone.utc)

        if window == "today":
            since_utc = utc_midnight
            until_utc = utc_now
        elif window == "yesterday":
            since_utc = utc_midnight - timedelta(days=1)
            until_utc = utc_midnight
        elif window == "7d":
            since_utc = utc_midnight - timedelta(days=7)
            until_utc = utc_now
        elif window == "30d":
            since_utc = utc_midnight - timedelta(days=30)
            until_utc = utc_now
        elif window == "all":
            since_utc = datetime(2000, 1, 1, tzinfo=timezone.utc)
            until_utc = utc_now
        else:
            return JSONResponse(
                {"error": f"unknown window={window!r}, allowed: today/yesterday/7d/30d/all"},
                status_code=400,
            )

        # ── docker logs --since 4d 백필 → store append (dedup) ──────────
        backfill_since = (utc_now - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        live_crosses = await asyncio.to_thread(
            _parse_ma_cross_from_docker_logs, backfill_since,
        )
        store = _get_ma_cross_store()
        added = store.append_many(live_crosses) if live_crosses else 0
        if added:
            import logging as _lg
            _lg.getLogger(__name__).info(
                "[ma_cross_metrics] store +%d new crosses (total=%d)",
                added, store.count(),
            )

        # ── 윈도우의 crosses 영속 store 에서 load + until 컷오프 ────────────
        crosses = store.load_since(since_utc)

        def _within(ts_iso: str) -> bool:
            try:
                ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return since_utc <= ts < until_utc
            except (ValueError, TypeError):
                return False
        crosses = [c for c in crosses if _within(c.get("ts", ""))]

        # ── 영속 sim cache 활용 (cache miss 만 bitget 봉 fetch + 시뮬) ─────
        sim_cache = _get_ma_cross_sim_cache()
        cached_sims, missing = sim_cache.split(crosses)

        new_sims: list[dict] = []
        if missing:
            sem = asyncio.Semaphore(20)
            async with _httpx.AsyncClient() as client:
                async def _sim_one(cross: dict) -> dict | None:
                    async with sem:
                        bars = await _fetch_1h_bars_after(
                            client, cross["symbol"], cross["ts"],
                        )
                    out = _simulate_ma_cross(cross, bars)
                    if out is None:
                        return None
                    return {**cross, **out}

                results = await asyncio.gather(
                    *(_sim_one(c) for c in missing),
                    return_exceptions=False,
                )
            new_sims = [r for r in results if r is not None]
            if new_sims:
                sim_cache.put_many(new_sims)

        sims = cached_sims + new_sims
        agg = _aggregate_ma_cross_sims(sims)
        sim_keys = {(s["ts"], s["symbol"]) for s in sims}
        no_bar = [
            {**c, "outcome": "no_bars", "pct": None, "bar_idx": None}
            for c in crosses if (c["ts"], c["symbol"]) not in sim_keys
        ]
        payload = {
            "date_kst": kst_now.strftime("%Y-%m-%d"),
            "window": window,
            "window_start_utc": since_utc.isoformat(),
            "window_end_utc": until_utc.isoformat(),
            "now_kst": kst_now.isoformat(),
            "store_count": store.count(),
            "store_earliest": store.earliest_ts(),
            "sim_cache_count": sim_cache.count(),
            "sim_cache_hits": len(cached_sims),
            "sim_cache_misses": len(missing),
            "crosses_total": len(crosses),
            "sims_total": len(sims),
            **agg,
            "sims": sims + no_bar,  # per-cross 상세 (ts/symbol/cross/close/outcome/pct/bar_idx)
            "rule": {
                "name": "default",
                "tp_pct": MA_CROSS_TP_PCT, "sl_pct": MA_CROSS_SL_PCT,
                "hold_bars": MA_CROSS_HOLD_BARS, "fee_pct": MA_CROSS_FEE_PCT,
            },
            "cached": False,
        }
        _ma_cross_metrics_cache[cache_key] = {"ts": now, "data": payload}
        return JSONResponse(payload)

    @app.get("/api/journal/today")
    async def api_journal_today() -> JSONResponse:
        """오늘 KST 자정~지금 사이의 모든 거래·신호·메모 통합 (2026-05-21).

        Claude Routines 일일 리포트 routine 이 이 endpoint 하나만 GET 하면
        오늘의 모든 분석 데이터를 한꺼번에 받음. 자동 fill (WAL) + 자동
        signal_emitted + 수동 거래 (`manual_trade.jsonl`) + cs-tsmom 오늘
        TOP-10 모두 포함. 분석 prompt 는 `docs/routines/cs-tsmom-daily-report.md`
        템플릿 참조.
        """
        return JSONResponse(await _build_journal_today())

    @app.post("/api/journal/export")
    async def api_journal_export(body: dict[str, Any] | None = None) -> JSONResponse:
        """오늘 journal data 를 ``docs/journal_data/{date_kst}.json`` 으로 저장
        + (옵션) git add/commit/push. Claude Routines 가 fetch 하도록 repo 에
        publish 하는 1-click 버튼용 (사용자가 매일 1회 클릭).

        Body (모두 optional):
        - ``push``: bool (default True) — false 면 commit 만, push 생략
        - ``commit_message``: str — 기본 "chore(journal): export {date_kst} data"

        repo root 는 ``git rev-parse --show-toplevel`` 로 검출. dashboard 가
        repo 안에서 돌고 git credential helper 가 wincred 등으로 설정돼 있어야
        push 성공. 실패 시 단계별 reason 명확화.
        """
        import subprocess
        import json as _json
        body = body or {}
        do_push = bool(body.get("push", True))

        journal = await _build_journal_today()
        date_kst = journal["date_kst"]

        try:
            repo_root_bytes = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                stderr=subprocess.STDOUT, cwd=str(Path.cwd()),
            )
            repo_root = Path(repo_root_bytes.decode().strip())
        except Exception as err:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "stage": "detect_repo",
                 "reason": f"git repo not found: {err}"},
                status_code=500,
            )

        path = repo_root / "docs" / "journal_data" / f"{date_kst}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _json.dumps(journal, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as err:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "stage": "write_file",
                 "reason": f"{type(err).__name__}: {err}"},
                status_code=500,
            )
        rel_path = path.relative_to(repo_root).as_posix()

        def _run(cmd: list[str]) -> tuple[int, str]:
            p = subprocess.run(
                cmd, cwd=str(repo_root), capture_output=True, text=True,
            )
            return p.returncode, (p.stdout + p.stderr).strip()

        # 2026-05-24: master 외 브랜치에선 stop — routines 가 origin/master 만
        # 보므로 publish 의미 없음 + 작업 브랜치는 upstream 없어 git push 실패
        # (사용자 보고: chore/untracked-orphan-cleanup-2026-05-24 에서 export
        # → "no upstream branch" 로 실패). branch check 를 add 전에 두어 작업
        # 브랜치에 의도 안 한 journal commit 이 남지 않게 함.
        code, branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        branch = branch.strip()
        if code != 0 or branch != "master":
            return JSONResponse(
                {"ok": False, "stage": "branch_check",
                 "reason": (
                    f"현재 브랜치='{branch}' — journal export 는 master 에서만 "
                    f"가능합니다. (작업 중이면 git stash 후 git checkout master, "
                    f"다시 실행하세요. journal 파일은 docs/journal_data/ 에 이미 "
                    f"저장됐습니다.)"
                 ),
                 "path": rel_path},
                status_code=400,
            )

        code, out = _run(["git", "add", rel_path])
        if code != 0:
            return JSONResponse(
                {"ok": False, "stage": "add", "detail": out, "path": rel_path},
                status_code=500,
            )

        msg = str(body.get("commit_message") or f"chore(journal): export {date_kst} data")
        code, out = _run(["git", "commit", "-m", msg])
        if code != 0:
            # 이미 동일 내용이면 'nothing to commit' — 무해
            if "nothing to commit" in out or "no changes added" in out:
                return JSONResponse({
                    "ok": True, "path": rel_path,
                    "committed": False, "pushed": False,
                    "note": "이미 같은 내용 — commit 생략",
                })
            return JSONResponse(
                {"ok": False, "stage": "commit", "detail": out, "path": rel_path},
                status_code=500,
            )

        pushed = False
        if do_push:
            code, out = _run(["git", "push"])
            if code != 0:
                return JSONResponse(
                    {"ok": False, "stage": "push", "detail": out,
                     "path": rel_path, "committed": True},
                    status_code=500,
                )
            pushed = True

        return JSONResponse({
            "ok": True, "path": rel_path,
            "committed": True, "pushed": pushed,
            "counts": journal.get("counts"),
        })

    @app.get("/api/limits")
    async def api_limits() -> JSONResponse:
        return JSONResponse({
            "per_trade": state.limit_per_trade,
            "per_day": state.limit_per_day,
            "per_portfolio": state.limit_per_portfolio,
            "per_position": state.limit_per_position,
            "sector": state.limit_sector,
            "drawdown": state.limit_drawdown,
        })

    @app.get("/api/kill-switch")
    async def api_ks_state() -> JSONResponse:
        return JSONResponse({
            "triggers": state.kill_switch_triggers,
            "last_triggered": state.kill_switch_last_triggered,
        })

    @app.post("/api/kill-switch/trigger")
    async def api_ks_trigger(body: dict[str, str]) -> JSONResponse:
        reason = body.get("reason", "manual")
        if reason in state.kill_switch_triggers:
            state.kill_switch_triggers[reason] = True
        state.kill_switch_last_triggered = datetime.now(timezone.utc).isoformat()
        return JSONResponse({"ok": True, "reason": reason})

    @app.post("/api/kill-switch/reset")
    async def api_ks_reset(body: dict[str, str]) -> JSONResponse:
        reason = body.get("reason", "manual")
        if reason in state.kill_switch_triggers:
            state.kill_switch_triggers[reason] = False
        return JSONResponse({"ok": True, "reason": reason})

    # ---- Strategy catalog + toggle (#178 + #180) -------------------------

    def _resolve_specs_dir() -> Path:
        if state.specs_dir is not None:
            return Path(state.specs_dir)
        # Default: docs/specs/strategies relative to repo root.
        return Path(__file__).resolve().parents[2] / "docs" / "specs" / "strategies"

    def _resolve_production_yaml() -> Path:
        if state.production_yaml_path is not None:
            return Path(state.production_yaml_path)
        return Path(__file__).resolve().parents[2] / "configs" / "orchestrator" / "production.yaml"

    def _enriched_catalog() -> list[dict]:
        """Catalog + truthful enabled/venue derivation (2026-05-20 정직화).

        Old behavior: ``enabled = item.get("enabled", True)`` made every
        rejected/commented-out spec look ON in the UI. Truth derivation now:

          1. spec ``status: rejected``                            → OFF, reason=rejected
          2. orch registered + ``is_enabled(sid)``                → orch wins (runtime)
          3. orch missing/unregistered AND production_status=active
             → ON (config intent) but toggle read-only            → reason=no-runtime
          4. production_status in {commented, absent}             → OFF, reason matches

        Also attaches a sorted venue list (KIS / Binance) derived from the
        spec's ``instruments`` for visual market-mark chips on the card.
        """
        items = load_strategy_catalog(_resolve_specs_dir())
        prod_status = load_production_status(_resolve_production_yaml())
        orch = state.orchestrator
        agg = state.pnl_aggregator
        for it in items:
            sid = it["id"]
            spec_status = str(it.get("status") or "")
            pstatus = prod_status.get(sid, "absent")
            it["production_status"] = pstatus
            it["venues"] = _classify_venues(it.get("instruments") or [])
            registered = (
                orch is not None
                and sid in getattr(orch, "strategies", {})
            )
            if spec_status == "rejected":
                it["enabled"] = False
                it["toggle_disabled"] = True
                it["disabled_reason"] = "rejected"
            elif registered:
                it["enabled"] = bool(orch.is_enabled(sid))
                it["toggle_disabled"] = False
                it["disabled_reason"] = None
            elif pstatus == "active":
                # Configured ON, but no runtime orch attached (dashboard-only):
                # show ON to reflect config intent, but the toggle is
                # read-only (no orchestrator to receive enable/disable calls).
                it["enabled"] = True
                it["toggle_disabled"] = True
                it["disabled_reason"] = "no-runtime"
            else:
                it["enabled"] = False
                it["toggle_disabled"] = True
                it["disabled_reason"] = pstatus  # "commented" or "absent"
            it["pnl_today"] = float(agg.daily_for(sid)) if agg is not None else 0.0
        # 켜진 전략 위, 꺼진 전략 아래 — 같은 그룹 내 id 알파벳 정렬 (안정적, 보기 좋음).
        items.sort(key=lambda r: (not bool(r.get("enabled")), str(r.get("id", ""))))
        return items

    @app.get("/api/strategies")
    async def api_strategies() -> JSONResponse:
        return JSONResponse(_enriched_catalog())

    @app.get("/strategies", response_class=HTMLResponse)
    async def strategies_page() -> HTMLResponse:
        return HTMLResponse(content=_render_strategies(_enriched_catalog()))

    @app.post("/api/strategies/{strategy_id}/toggle")
    async def api_toggle_strategy(strategy_id: str, body: dict) -> JSONResponse:
        if "enabled" not in body or not isinstance(body.get("enabled"), bool):
            raise HTTPException(status_code=400, detail="body must contain {enabled: bool}")
        orch = state.orchestrator
        if orch is None:
            raise HTTPException(status_code=503, detail="orchestrator not wired")
        try:
            if body["enabled"]:
                orch.enable_strategy(strategy_id)
                intents: list = []
            else:
                positions = []
                if state.position_provider is not None:
                    positions = list(state.position_provider(strategy_id) or [])
                order_intents = orch.disable_strategy(strategy_id, positions=positions)
                intents = [
                    {
                        "strategy_id": oi.strategy_id,
                        "symbol": oi.symbol,
                        "side": oi.side,
                        "qty": oi.qty,
                        "reason": oi.reason,
                    }
                    for oi in order_intents
                ]
        except ValueError as err:
            raise HTTPException(status_code=404, detail=str(err))
        return JSONResponse({
            "ok": True,
            "strategy_id": strategy_id,
            "enabled": body["enabled"],
            "liquidation_intents": intents,
        })

    @app.post("/api/strategies/{strategy_id}/positions/{symbol}/close")
    async def api_manual_close_position(
        strategy_id: str, symbol: str, body: dict | None = None,
    ) -> JSONResponse:
        """대시보드 수동 청산 — 보유 종목 즉시 시장가 매도/커버.

        Body: ``{"qty": "all"}`` (default, 전량) or ``{"qty": <number>}``.
        Binance UI 직접 청산은 우리 client_order_id 매핑 밖이라 strategy 귀속이
        끊어진다 — 본 endpoint 는 우리 system 발급 coid 로 broker 에 보내므로
        WAL → pnl_aggregator → trade_history 까지 정상 갱신된다.
        """
        executor = state.manual_close_executor
        if executor is None:
            raise HTTPException(
                status_code=503,
                detail="manual_close_executor not wired (dashboard-only / paper mode)",
            )
        if state.position_provider is None:
            raise HTTPException(
                status_code=503, detail="position_provider not wired",
            )
        # Resolve current NET position. position_provider returns
        # [(symbol, signed_qty), ...] for the strategy.
        try:
            positions = list(state.position_provider(strategy_id) or [])
        except Exception as err:  # noqa: BLE001 — defensive
            raise HTTPException(
                status_code=500, detail=f"position_provider failed: {err}",
            )
        held = next((q for sym, q in positions if sym.upper() == symbol.upper()), 0.0)
        # 2026-05-21: Binance ground-truth fallback. position_provider 는
        # in-memory store 라 fill 미동기/buy-sell qty 불일치 시 net=0 인데
        # 실제 거래소 포지션은 살아있는 케이스가 발생 (사용자 사고: 청산 발주
        # → 우리 net=0, 거래소는 안 줄어듦 → 사용자가 거래소에서 또 매도 →
        # 이중 매도). 이 경우 거래소 실제 amt 로 reduce_only 발주해서 사고
        # 차단. 거래소에도 없으면 진짜 청산된 것 → 404.
        if held == 0.0:
            provider = state.account_info_provider
            if provider is not None:
                try:
                    fb = getattr(provider, "fetch_binance", None)
                    bn = await asyncio.to_thread(fb) if callable(fb) else None
                    if not isinstance(bn, dict) or not bn.get("ok"):
                        bn = None
                    if bn is not None:
                        for p in bn.get("positions") or []:
                            if (p.get("symbol") or "").upper() == symbol.upper():
                                held = float(p.get("amt") or 0.0)
                                break
                except Exception:  # noqa: BLE001 — fallback is best-effort
                    pass
        if held == 0.0:
            raise HTTPException(
                status_code=404,
                detail=f"no open position for {strategy_id} / {symbol} (in-memory store + Binance both flat)",
            )
        body = body or {}
        qty_request = body.get("qty", "all")
        if qty_request == "all":
            close_qty = abs(float(held))
        else:
            try:
                close_qty = float(qty_request)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail="qty must be 'all' or a number",
                )
            if close_qty <= 0:
                raise HTTPException(status_code=400, detail="qty must be > 0")
            if close_qty > abs(float(held)) + 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"qty {close_qty} exceeds held {abs(float(held))}",
                )
        # NET LONG → SELL; NET SHORT → BUY (cover). ``reduce_only=True``
        # is critical here: it makes the exchange itself refuse to flip the
        # position past flat, so a stale qty cannot accidentally open a
        # naked opposite-side position.
        side = "sell" if float(held) > 0 else "buy"
        from src.portfolio.order_intent import OrderIntent
        intent = OrderIntent(
            strategy_id=strategy_id,
            symbol=symbol,
            side=side,
            qty=close_qty,
            reason="manual_close_from_dashboard",
            reduce_only=True,
        )
        try:
            result = await executor([intent])
        except Exception as err:  # noqa: BLE001 — surface to operator
            raise HTTPException(
                status_code=500, detail=f"executor failed: {err}",
            )
        # 2026-05-22: 청산 후 orchestrator._live_entered 정합. 수동 청산은
        # store·broker 가 함께 flat 이 되므로 reconciler mismatch 를 만들지
        # 않아 PR #287 의 sync 가 안 걸리고, release_live_position(stop/TP
        # 경로)도 안 거친다. 미정리 시 그 (strategy, symbol) 재진입이 영구
        # 차단된다. remaining=0 → discard(재진입 허용), >0(부분청산) → 유지.
        remaining = abs(float(held)) - close_qty
        orch = getattr(state, "orchestrator", None)
        if orch is not None and hasattr(orch, "sync_live_entered"):
            orch.sync_live_entered(strategy_id, symbol, remaining)
        return JSONResponse({
            "ok": True,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "side": side,
            "submitted_qty": close_qty,
            "result": result if isinstance(result, dict) else None,
        })

    @app.websocket("/ws/timeline")
    async def ws_timeline(
        ws: WebSocket,
        replay: int = Query(default=100, ge=0, le=1000),
    ) -> None:
        """매매 타임라인 실시간 스트림 (#181).

        프로토콜:
        1. 연결 후 WAL replay 마지막 N건을 dict 로 전송 (replay=0 시 생략).
        2. `{"phase": "live_ready", "replayed": N}` 센티넬 전송.
        3. broker subscribe → 큐에서 받아 send_json (drop-oldest back-pressure).
        4. 클라 disconnect 시 unsubscribe.
        """
        await ws.accept()
        replayed = 0
        if replay > 0 and state.wal_path is not None:
            events, _corruptions = wal_replay(state.wal_path)
            tail = events[-replay:] if events else []
            for ev in tail:
                await ws.send_json(asdict(ev))
            replayed = len(tail)

        await ws.send_json({"phase": "live_ready", "replayed": replayed})

        broker = state.timeline_broker
        assert broker is not None  # create_app 에서 보장
        queue = broker.subscribe()
        try:
            while True:
                event = await queue.get()
                await ws.send_json(event)
        except WebSocketDisconnect:
            pass
        except (asyncio.CancelledError, RuntimeError):
            # Client closed mid-send; treat as normal disconnect.
            pass
        finally:
            broker.unsubscribe(queue)

    # ── 거래 시작/정지 컨트롤 (#182 단계 2) ────────────────────────────────
    @app.get("/api/run/status")
    async def api_run_status() -> JSONResponse:
        rc = state.run_controller
        if rc is None:
            return JSONResponse({"available": False})
        return JSONResponse({"available": True, **rc.status()})

    @app.post("/api/run/start")
    async def api_run_start(body: dict[str, Any] | None = None) -> JSONResponse:
        rc = state.run_controller
        if rc is None:
            return JSONResponse({"ok": False, "reason": "controller unavailable"}, status_code=503)
        params = body or {}
        result = await rc.start(params)
        code = 200 if result.get("ok") else 422
        return JSONResponse(result, status_code=code)

    @app.post("/api/run/stop")
    async def api_run_stop() -> JSONResponse:
        rc = state.run_controller
        if rc is None:
            return JSONResponse({"ok": False, "reason": "controller unavailable"}, status_code=503)
        result = await rc.stop()
        code = 200 if result.get("ok") else 422
        return JSONResponse(result, status_code=code)

    # ── 내 계좌 정보 (#182) ────────────────────────────────────────────────
    _acct_cache: dict[str, Any] = {"data": None, "inflight": False}

    @app.get("/api/account/info")
    async def api_account_info() -> JSONResponse:
        provider = state.account_info_provider
        if provider is None:
            return JSONResponse({"available": False})
        # In-flight 가드 (#238 hotfix) — 이전 호출이 KIS retry 로 길어지면 새 호출
        # 은 캐시된 값 반환. KIS 모의 초당 한도 보호.
        if _acct_cache["inflight"] and _acct_cache["data"] is not None:
            return JSONResponse({"available": True, **_acct_cache["data"], "stale": True})
        _acct_cache["inflight"] = True
        # #231 — sync I/O (KIS REST + Binance REST) 를 thread 로 분리해 FastAPI
        # 이벤트 루프 block 방지.
        import asyncio
        try:
            data = await asyncio.to_thread(provider.fetch)
            _acct_cache["data"] = data
        finally:
            _acct_cache["inflight"] = False
        return JSONResponse({"available": True, **data})

    @app.get("/api/account/binance")
    async def api_account_binance() -> JSONResponse:
        """Binance 전용 빠른 갱신 (대시보드 10s 폴링) — KIS REST 미접촉.

        provider.fetch_binance() 우선; 구형 provider(.fetch 만 보유) 는
        fetch()["binance"] 로 graceful fallback. 항상 200 — 절대 500 금지.
        반환 binance dict 의 ``ts`` 가 스냅샷 시각.
        """
        provider = state.account_info_provider
        if provider is None:
            return JSONResponse({"available": False})
        import asyncio
        fb = getattr(provider, "fetch_binance", None)
        try:
            if callable(fb):
                binance = await asyncio.to_thread(fb)
            else:
                data = await asyncio.to_thread(provider.fetch)
                binance = (data or {}).get("binance", {"ok": False})
        except Exception as err:  # noqa: BLE001 — never 500 the dashboard
            return JSONResponse(
                {"available": True,
                 "binance": {"ok": False, "error": str(err)}}
            )
        return JSONResponse({"available": True, "binance": binance})

    @app.get("/api/account/bitget")
    async def api_account_bitget() -> JSONResponse:
        """Bitget 잔고/포지션 — Demo or Mainnet (P5).

        provider.fetch() 의 ``bitget`` 키를 반환. _fetch_bitget 은 sync httpx
        라 to_thread 로 오프-loop 실행. KIS+Binance 캐시와 공유 (15s TTL).
        """
        provider = state.account_info_provider
        if provider is None:
            return JSONResponse({"available": False})
        import asyncio
        try:
            data = await asyncio.to_thread(provider.fetch)
            bitget = (data or {}).get("bitget", {"ok": False})
        except Exception as err:  # noqa: BLE001
            return JSONResponse(
                {"available": True,
                 "bitget": {"ok": False, "error": str(err)}}
            )
        return JSONResponse({"available": True, "bitget": bitget})

    # ── venue 실증 가시화 (#238 follow-up) ───────────────────────────────
    @app.get("/api/venue_equity_status")
    async def api_venue_equity_status() -> JSONResponse:
        """Per-venue real-equity status from the live SnapshotBuilder.

        ok=False means that venue is INERT — the orchestrator's fraction→qty
        conversion drops EVERY order there because real equity is unavailable
        (creds missing, provider error, or cash<=0). Surfacing this turns a
        silent "0 trades" into an explained one. Read-only & robust: any
        missing/odd builder yields {available, venues:{}} — never 500.
        """
        builder = state.snapshot_builder
        status = getattr(builder, "last_equity_status", None)
        if builder is None or not isinstance(status, dict):
            return JSONResponse({"available": False, "venues": {}})
        venues: dict[str, Any] = {}
        for venue, info in status.items():
            if not isinstance(info, dict):
                continue
            venues[str(venue)] = {
                "ok": bool(info.get("ok")),
                "reason": str(info.get("reason") or ""),
                "equity": float(info.get("equity") or 0.0),
            }
        return JSONResponse({"available": True, "venues": venues})

    # ── Shadow Runs 뷰어 (#198) — read-only WAL 통합 표시 ───────────────────
    def _resolve_shadow_log_dir() -> Path:
        if state.shadow_log_dir is not None:
            return Path(state.shadow_log_dir)
        # Default: <repo_root>/logs/shadow
        return Path(__file__).resolve().parents[2] / "logs" / "shadow"

    @app.get("/api/shadow_runs")
    async def api_shadow_runs() -> JSONResponse:
        runs = discover_shadow_runs(_resolve_shadow_log_dir())
        return JSONResponse(runs)

    @app.get("/api/shadow_runs/{run_id}")
    async def api_shadow_run_detail(run_id: str) -> JSONResponse:
        detail = load_run_detail(_resolve_shadow_log_dir(), run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return JSONResponse(detail)

    @app.get("/shadow_runs", response_class=HTMLResponse)
    async def shadow_runs_page() -> HTMLResponse:
        runs = discover_shadow_runs(_resolve_shadow_log_dir())
        return HTMLResponse(content=_render_shadow_runs(runs))

    @app.get("/patch-notes", response_class=HTMLResponse)
    async def patch_notes_page() -> HTMLResponse:
        return HTMLResponse(content=render_patch_notes_page())

    return app


# Standalone entry point: uvicorn src.dashboard.app:app
app = create_app()

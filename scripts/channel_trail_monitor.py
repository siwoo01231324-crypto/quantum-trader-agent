#!/usr/bin/env python3
"""channel_trail_monitor.py — 수동 돌파 롱 포지션 채널 트레일링 감시 (알림 전용).

돌파 전략(live-donchian-breakout-btcgate)의 정석 청산 = Donchian10 하단 채널.
사용자가 재량으로 수동 진입한 돌파 롱들을 대상으로, 매 4h봉 마감마다 채널선을
재계산해 **트레일업(손절 상향)이 필요한 순간 · 청산 신호가 뜬 순간에만** 텔레그램
알림을 보낸다. 조정이 필요 없으면 조용히 지나간다(매 4h 정기보고 안 함).

⚠️ 불변식 #6 준수 — 이 스크립트는 **주문을 절대 걸거나 수정하지 않는다**. 채널선
계산 + 텔레그램 알림만. 실제 SL 조정은 사람이 거래소에서 직접 한다.

청산 철학 = **A (터치 SL 트레일)** — 2026-07-16 사용자 확정.
  거래소 터치식 SL 을 채널선까지 위로만 끌어올린다(상시 보호 + 4h 사이 폭락 갭
  방어 우선, 꼬리 휩쏘는 감수). 종가식 채널청산(B)은 갭 무방비라 미채택.
  → 🟢 트레일업이 주 액션(SL 상향). 🔴 은 "SL 이 나갔어야 하는데 포지션이 아직
    열려있음"(터치SL 미체결 갭/글리치) 안전망 경보 — 정상 시엔 안 뜬다.

동작:
  - Bitget 실계좌 오픈 포지션(WATCH_SYMBOLS 중 롱) + 각 포지션에 걸린 실제 SL(plan
    order pos_loss)을 읽는다.
  - 4h 공개 캔들로 Donchian10 하단(= 트레일링 손절 레벨)을 계산.
  - 트레일업(🟢, **이익잠금 전용**): 채널선이 **진입가+수수료(0.2%) 위**로 올라왔고,
    기존 SL 보다 위이며, 현재가보다 0.3% 이상 아래일 때만 "손절 올려" 알림.
    → SL 이동은 항상 본전 이상을 잠근다. 채널선이 아직 진입가 아래면 **알림 안 함**
    (초기 2ATR 손절 유지 — 손실구간에서 손절만 조이면 휩쏘 확률만 2배).
  - SL 안전망(🔴): 직전 마감 4h 종가가 채널 아래인데 포지션이 아직 열려있으면
    (= 터치 SL 미체결 의심) 즉시확인 경보.
  - 알림 dedup: logs/channel_trail_state.json 에 마지막 알림 채널/청산봉 기록.

실행: python scripts/channel_trail_monitor.py   (셀프 4h 정렬 루프)
      python scripts/channel_trail_monitor.py --once   (1회만)
      python scripts/channel_trail_monitor.py --dry-run (텔레그램 미발송, 콘솔만)

WATCH_SYMBOLS 기본 = 사용자 6개 수동 돌파 롱. env CHANNEL_TRAIL_SYMBOLS 로 override
(쉼표구분). 봇이 같은 종목을 별도 진입(네팅)하면 봇 native TP/SL 과 겹칠 수 있으니
주의 — 이 감시는 '수동 오펀 포지션' 전용.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

log = logging.getLogger("channel_trail")

EXIT_LOOKBACK = 10          # Donchian 하단 lookback (전략 exit_lookback 과 동일)
BOUNDARY_OFFSET_SEC = 120   # 4h 마감 후 캔들 확정 대기 여유
_EPS = 0.001                # 트레일업 최소 상향폭 (0.1%) — 미세변동 스팸 차단

# ── 이익잠금 전용 트레일링 (2026-07-16, 사용자 정정) ──────────────────────────
# 이전 로직은 채널선이 SL 위이기만 하면 무조건 "SL 올려" 알림 → 채널선이 아직
# **진입가 아래**여도 손절선만 조여서(예: −3.4% → −2.3%) 휩쏘 확률만 2배 되고
# 털리면 여전히 손실이었다. 사용자 의도는 "가격이 진입가 위로 충분히 올랐을 때
# 이익을 확정하려고 SL 을 올리는 것" → SL 이동은 **항상 본전 이상을 잠글 때만**.
#   · 채널선 < 진입가+수수료  → 알림 안 함 (초기 2ATR 손절 그대로, 휩쏘 회피)
#   · 채널선 ≥ 진입가+수수료  → 그때부터 트레일업 (올리는 순간 최소 본전 확정)
_FEE_BUFFER_PCT = 0.002     # 왕복 수수료+슬리피지 여유 (0.2%) — 이 위여야 실이익
# 채널선이 현재가에 너무 붙거나 위면 SL 을 시장가 위/근처에 거는 셈 → 즉시 체결
# ·거래소 거절. 최소 이 정도는 현재가 아래여야 트레일업 알림.
_MIN_SL_GAP_PCT = 0.003     # 0.3%
_DEFAULT_SYMBOLS = ["ETHUSDT", "ZECUSDT", "XRPUSDT", "LINKUSDT", "FILUSDT", "INJUSDT"]
_STATE_PATH = _ROOT / "logs" / "channel_trail_state.json"
_BASE = "https://api.bitget.com"


def _autoload_env() -> None:
    env = _ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _creds() -> tuple[str, str, str]:
    return (
        os.environ["BITGET_API_KEY"],
        os.environ["BITGET_API_SECRET"],
        os.environ["BITGET_API_PASSPHRASE"],
    )


def _signed_get(path: str) -> dict:
    k, s, p = _creds()
    ts = str(int(time.time() * 1000))
    sign = base64.b64encode(
        hmac.new(s.encode(), (ts + "GET" + path).encode(), hashlib.sha256).digest()
    ).decode()
    req = urllib.request.Request(
        _BASE + path,
        headers={
            "ACCESS-KEY": k, "ACCESS-SIGN": sign, "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": p, "locale": "en-US",
        },
    )
    return json.load(urllib.request.urlopen(req, timeout=15))


def _candles(symbol: str, limit: int = 40) -> list[list[float]]:
    """4h OHLCV (공개). 반환: 오래된→최신 순 [[ts,o,h,l,c,v], ...]."""
    url = (f"{_BASE}/api/v2/mix/market/candles?symbol={symbol}"
           f"&productType=USDT-FUTURES&granularity=4H&limit={limit}")
    data = json.load(urllib.request.urlopen(url, timeout=15)).get("data", [])
    return [[float(x[0]), *(float(v) for v in x[1:6])] for x in data]


def _open_long_positions(symbols: set[str]) -> dict[str, dict]:
    """WATCH 중 오픈 롱 포지션 {sym: {entry, total}}."""
    d = _signed_get(
        "/api/v2/mix/position/all-position?productType=USDT-FUTURES&marginCoin=USDT"
    )
    out: dict[str, dict] = {}
    for p in d.get("data", []) or []:
        sym = p.get("symbol")
        if sym not in symbols:
            continue
        total = float(p.get("total", 0) or 0)
        if total == 0 or str(p.get("holdSide", "")).lower() != "long":
            continue
        out[sym] = {"entry": float(p.get("openPriceAvg", 0) or 0), "total": total}
    return out


def _position_sl(symbols: set[str]) -> dict[str, float]:
    """포지션에 걸린 실제 손절(pos_loss trigger) {sym: sl_price}."""
    try:
        d = _signed_get(
            "/api/v2/mix/order/orders-plan-pending"
            "?productType=USDT-FUTURES&planType=profit_loss"
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("SL plan order 읽기 실패: %s", exc)
        return {}
    rows = d.get("data", {})
    items = rows.get("entrustedList") if isinstance(rows, dict) else rows
    out: dict[str, float] = {}
    for it in items or []:
        sym = it.get("symbol")
        if sym not in symbols:
            continue
        stop_type = it.get("planType") or it.get("stopType") or ""
        if "loss" in str(stop_type).lower():
            try:
                out[sym] = float(it.get("triggerPrice"))
            except (TypeError, ValueError):
                pass
    return out


def _channel_levels(candles: list[list[float]]) -> tuple[float, float, float, float]:
    """(channel_now, prior_channel, last_closed_close, last_closed_ts).

    channel_now      = 최근 10 마감봉 저점 최소 = 다음 봉의 트레일링 손절 레벨.
    prior_channel    = 직전 마감봉 이전 10봉 저점 = 그 마감봉의 exit 판정 기준.
    last_closed_close= 직전 마감봉 종가 (형성봉 제외).
    """
    closed = candles[:-1]                     # 형성(미완성) 봉 제거
    lows = [c[3] for c in closed]
    closes = [c[4] for c in closed]
    ts = [c[0] for c in closed]
    channel_now = min(lows[-EXIT_LOOKBACK:])
    prior_channel = min(lows[-(EXIT_LOOKBACK + 1):-1])
    return channel_now, prior_channel, closes[-1], ts[-1]


def _load_state() -> dict:
    if _STATE_PATH.exists():
        try:
            return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


_EXCLUDE_PATH = _ROOT / "config" / "breakout_exclude.json"


def _sync_breakout_exclude(open_symbols: set[str]) -> bool:
    """봇 돌파 유니버스 제외 목록을 현재 열린 수동 포지션에 동기화.

    `config/breakout_exclude.json` 에 `{"exclude": [열린 WATCH 심볼]}` 을 쓴다.
    수동 포지션 청산 → open_symbols 에서 빠짐 → 파일에서 제거 → 봇이 다음 유니버스
    갱신(~5분)에 그 종목 돌파 감시 재개(재시작 불필요). 내용 변화 시에만 write.
    반환 = 파일이 실제로 바뀌었으면 True.
    """
    desired = sorted(open_symbols)
    try:
        cur = json.loads(_EXCLUDE_PATH.read_text(encoding="utf-8")).get("exclude", []) \
            if _EXCLUDE_PATH.exists() else []
    except Exception:  # noqa: BLE001
        cur = None
    if sorted(str(s).upper() for s in (cur or [])) == desired:
        return False
    try:
        _EXCLUDE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _EXCLUDE_PATH.write_text(json.dumps({
            "_comment": ("봇 돌파 자동진입 제외(수동 보유 종목). channel_trail_monitor 가 "
                         "열린 수동 포지션에 자동 동기화. 수동 청산 시 자동 제거."),
            "exclude": desired,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("breakout_exclude 동기화 실패: %s", exc)
        return False


def run_check(symbols: list[str], *, dry_run: bool = False) -> list[str]:
    """1회 채널 점검. 반환 = 알림 라인 리스트(빈 리스트면 조정 불필요)."""
    from telegram_alert import send_telegram  # noqa: PLC0415 — .env autoload 후 import

    symset = set(symbols)
    positions = _open_long_positions(symset)
    sls = _position_sl(symset)
    # 봇 돌파 유니버스 제외 = 현재 열린 수동 포지션에 자동 동기화(청산 시 자동 복원).
    if _sync_breakout_exclude(set(positions.keys())):
        log.info("breakout_exclude 갱신: %s", sorted(positions.keys()))
    state = _load_state()
    alerts: list[str] = []

    for sym in symbols:
        pos = positions.get(sym)
        if pos is None:
            continue                          # 포지션 없음(청산됨/미보유) → skip
        try:
            candles = _candles(sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s 캔들 실패: %s", sym, exc)
            continue
        channel_now, prior_channel, last_close, bar_ts = _channel_levels(candles)
        st = state.setdefault(sym, {})
        cur_sl = sls.get(sym)

        # ① SL 안전망 (철학 A) — 4h 종가가 채널 아래인데 포지션이 아직 열려있음
        # = 터치 SL 이 나갔어야 하는데 미체결(갭/글리치). 봉당 1회.
        if last_close < prior_channel and st.get("exit_bar") != bar_ts:
            st["exit_bar"] = bar_ts
            alerts.append(
                f"🔴 *{sym} SL 안전망 경보* — 4h 종가 {last_close:.5g} 가 채널선 "
                f"{prior_channel:.5g} 아래 마감인데 포지션이 아직 열려있음. "
                f"터치 SL 미체결(갭/글리치) 의심 → 즉시 확인·수동청산."
            )
            continue

        # ② 트레일업 — **이익잠금 전용**. 아래 4조건 모두 만족해야 알림.
        if cur_sl is None:
            continue
        entry = float(pos["entry"])
        lock_floor = entry * (1 + _FEE_BUFFER_PCT)   # 이 위여야 올려도 실이익 확정
        last_alert = float(st.get("alerted_channel", 0) or 0)
        last_price = float(candles[-1][4])           # 형성봉 현재가
        if (
            channel_now >= lock_floor                       # (1) 본전+수수료 위 = 이익 잠금
            and channel_now > cur_sl * (1 + _EPS)           # (2) 기존 SL 보다 위 (래칫)
            and channel_now > last_alert * (1 + _EPS)       # (3) 지난 알림 대비 유의미 상승(스팸 차단)
            and channel_now < last_price * (1 - _MIN_SL_GAP_PCT)  # (4) 현재가보다 충분히 아래
        ):
            st["alerted_channel"] = channel_now
            gain = (channel_now / entry - 1) * 100
            alerts.append(
                f"🟢 *{sym} 손절 올려(이익확정)* — SL {cur_sl:.5g} → *{channel_now:.5g}* "
                f"(진입 {entry:.5g} 대비 {gain:+.1f}% → 여기서 털려도 **이익**). "
                f"현재가 {last_price:.5g}."
            )

    if alerts and not dry_run:
        send_telegram(
            "📈 *채널 트레일링 알림*\n\n" + "\n\n".join(alerts)
            + "\n\n_계산·알림 전용 — 실제 SL 조정은 직접 하세요._"
        )
    _save_state(state)
    return alerts


def _seconds_to_next_boundary() -> float:
    now = datetime.now(timezone.utc)
    next_h = (now.hour // 4 + 1) * 4
    if next_h >= 24:
        nb = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        nb = now.replace(hour=next_h, minute=0, second=0, microsecond=0)
    return (nb - now).total_seconds() + BOUNDARY_OFFSET_SEC


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="1회만 점검하고 종료")
    ap.add_argument("--dry-run", action="store_true", help="텔레그램 미발송, 콘솔만")
    args = ap.parse_args()

    # 싱글턴 가드 — 이미 도는 인스턴스가 있으면 중복 실행(텔레그램 중복발송) 방지.
    # 프로세스 *이름*이 python 인 것만 카운트 → nohup/bash 래퍼(cmdline 에 'python'
    # 인자가 있어도 name 은 nohup.exe/bash.exe)는 제외. 자기 래퍼 보고 자살 방지.
    try:
        import psutil  # noqa: PLC0415
        me = os.getpid()
        dup = [
            p.pid for p in psutil.process_iter(["pid", "name", "cmdline"])
            if p.pid != me
            and (p.info["name"] or "").lower().startswith("python")
            and any("channel_trail_monitor.py" in str(x) for x in (p.info["cmdline"] or []))
        ]
        if dup and not args.once:
            log.warning("이미 감시 인스턴스 가동중 PID=%s — 중복실행 방지 종료", dup)
            return 0
    except Exception:  # noqa: BLE001 — psutil 없어도 계속 진행
        pass

    _autoload_env()
    env_syms = os.environ.get("CHANNEL_TRAIL_SYMBOLS", "").strip()
    symbols = [s.strip() for s in env_syms.split(",") if s.strip()] or _DEFAULT_SYMBOLS
    log.info("channel_trail_monitor 감시 대상: %s (dry_run=%s)", symbols, args.dry_run)

    if args.once:
        alerts = run_check(symbols, dry_run=args.dry_run)
        log.info("점검 완료 — 알림 %d건", len(alerts))
        for a in alerts:
            print(a)
        return 0

    while True:
        try:
            alerts = run_check(symbols, dry_run=args.dry_run)
            if alerts:
                log.info("알림 %d건 발송", len(alerts))
                for a in alerts:
                    log.info("  %s", a.replace("*", "").replace("\n", " "))
            else:
                log.info("변동없음 — 조정 불필요")
        except Exception as exc:  # noqa: BLE001 — 한 번 실패가 루프 안 죽인다
            log.exception("run_check 실패(다음 주기 재시도): %s", exc)
        # 15분 주기 — 채널알림은 dedup 이라 스팸 없고, exclude 동기화·청산 감지가
        # 빨라진다(4h 마감 사이 청산돼도 ~15분 내 봇 유니버스 복원).
        time.sleep(900)


if __name__ == "__main__":
    raise SystemExit(main())

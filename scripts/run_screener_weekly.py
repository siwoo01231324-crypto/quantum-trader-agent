"""저평가·우량주 스크리너 주간 자동 실행 러너 (Docker 컨테이너 엔트리포인트).

동작:
  - 시작 시 1회 즉시 실행 (SCREENER_RUN_ON_START=0 이면 생략)
  - 이후 매주 지정 요일·시각(기본 월요일 08:00 KST)에 _value_screener.py 실행
  - 실행마다 reports/value_screener.json + history.jsonl 갱신
  - 텔레그램(TELEGRAM_QTA_*)으로 요약(통과수·신규진입·이탈·top픽) 전송 (env 있으면)

컨테이너는 restart: unless-stopped 로 상시 가동. 크래시해도 다음 주기 재시도.
스케줄 env: SCREENER_WEEKDAY(0=월), SCREENER_HOUR(0-23), SCREENER_RUN_ON_START(0/1).

로컬 테스트: python scripts/run_screener_weekly.py --once   (1회만 실행하고 종료)
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
SCREENER = ROOT / "scripts" / "_value_screener.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ 임포트용

WEEKDAY = int(os.environ.get("SCREENER_WEEKDAY", "0"))   # 0=월요일
HOUR = int(os.environ.get("SCREENER_HOUR", "8"))         # 08:00
RUN_ON_START = os.environ.get("SCREENER_RUN_ON_START", "1") == "1"


def log(msg: str) -> None:
    print(f"[weekly] {datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def send_telegram(text: str) -> None:
    """레포 표준 텔레그램 발송 재사용 (LIVE→QTA→legacy 폴백 체인, telegram_alert.py)."""
    try:
        from telegram_alert import send_telegram as _send  # scripts/telegram_alert.py
        ok = _send(text, parse_mode="HTML")
        if not ok:
            log("텔레그램 발송 skip/실패 (자격증명 미설정 또는 API 오류)")
    except Exception as e:  # noqa: BLE001
        log(f"텔레그램 예외: {e}")


def build_summary() -> str:
    """reports/value_screener.json → 텔레그램 요약 HTML."""
    path = REPORTS / "value_screener.json"
    if not path.exists():
        return "스크리너 결과 파일 없음"
    d = json.loads(path.read_text(encoding="utf-8"))
    date = d.get("date", "?")
    uni = d.get("universe_size", "?")
    surv = d.get("survivors_total", "?")
    picks = d.get("picks", [])[:10]
    diff = d.get("diff_vs_prev")

    def esc(s):  # HTML 특수문자 이스케이프 (KT&G·S&T모티브 등)
        return html.escape(str(s))

    lines = [f"📊 <b>저평가·우량주 스크리너</b> ({esc(date)})",
             f"유니버스 {uni} → 최종통과 <b>{surv}</b>종목"]
    if diff:
        ent = ", ".join(esc(_name_of(d, c)) for c in diff.get("entered", [])) or "없음"
        exi = ", ".join(esc(nm) for _, nm in diff.get("exited", [])) or "없음"
        lines.append(f"🆕 신규진입: {ent}")
        lines.append(f"👋 이탈: {exi}")
    lines.append("\n<b>Top 10 (밸류×퀄리티)</b>")
    for i, p in enumerate(picks, 1):
        per = p.get("PER"); roe = p.get("ROE%"); mos = p.get("안전마진%")
        lines.append(f"{i}. {esc(p['name'])} — PER {per} · ROE {roe}% · 안전마진 {mos}%")
    lines.append("\n※ 후보 발굴용 — 매수는 사업·촉매 정성판단 병행")
    return "\n".join(lines)


def _name_of(d: dict, code: str) -> str:
    for p in d.get("picks", []):
        if p["code"] == code:
            return p["name"]
    return code


def run_once() -> bool:
    log("스크리너 실행 시작")
    try:
        child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(
            [sys.executable, str(SCREENER)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=3600,
            encoding="utf-8", errors="replace", env=child_env,
        )
        tail = "\n".join(proc.stdout.splitlines()[-25:])
        log(f"스크리너 종료 (exit={proc.returncode})\n{tail}")
        if proc.returncode != 0:
            log(f"stderr tail:\n{proc.stderr[-800:]}")
            send_telegram(f"⚠️ 스크리너 실행 실패 (exit={proc.returncode})")
            return False
        send_telegram(build_summary())
        return True
    except Exception as e:  # noqa: BLE001
        log(f"실행 예외: {e}")
        send_telegram(f"⚠️ 스크리너 러너 예외: {e}")
        return False


def seconds_until_next() -> float:
    now = datetime.now()
    # 이번 주 목표 요일·시각
    days_ahead = (WEEKDAY - now.weekday()) % 7
    target = (now + timedelta(days=days_ahead)).replace(
        hour=HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=7)
    return (target - now).total_seconds()


def main() -> None:
    if "--once" in sys.argv:
        run_once()
        return

    log(f"주간 러너 시작 — 매주 {['월','화','수','목','금','토','일'][WEEKDAY]}요일 {HOUR:02d}:00 KST")
    if RUN_ON_START:
        run_once()

    while True:
        secs = seconds_until_next()
        nxt = datetime.now() + timedelta(seconds=secs)
        log(f"다음 실행까지 {secs/3600:.1f}시간 대기 (예정 {nxt:%Y-%m-%d %H:%M})")
        time.sleep(secs)
        run_once()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Issue #133 Phase 2 운영 Telegram alert utility.

Modes (mutually exclusive):
  --watch DIR    Polling 5s on DIR/wal.jsonl; sends Telegram on critical WAL events
                 (mode_switched / fill_anomaly / order_rejected with KILL_SWITCH reason).
  --report PATH  Send daily report markdown summary (KST 16:00 cron 발화 직후).
  --test         Send a test ping message.

Env (#152 fallback chain — 모든 알림 단일 LIVE 채널로 통일):
  TELEGRAM_LIVE_BOT_TOKEN / TELEGRAM_LIVE_CHAT_ID   1순위 (현 운영 표준)
  TELEGRAM_QTA_BOT_TOKEN  / TELEGRAM_QTA_CHAT_ID    2순위 (#133 초기 표준)
  TELEGRAM_BOT_TOKEN      / TELEGRAM_CHAT_ID        legacy fallback
  토큰/chat_id 둘 중 하나라도 빠지면 warn + skip — daemon halt 안 함.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

# WAL 의 critical event_type 화이트리스트 — Telegram 즉시 발송 대상.
# `position_stop_triggered` 는 #227 LivePositionRiskManager 가 stop_loss /
# take_profit / trailing_stop 발동 시 발행 — 즉시 사용자에게 통지.
CRITICAL_EVENT_TYPES = {"mode_switched", "fill_anomaly", "position_stop_triggered"}
# Telegram sendMessage 본문 최대 4096 자 — 안전 마진 96 자.
TELEGRAM_MAX_LEN = 4000

log = logging.getLogger("telegram_alert")


def _resolve_telegram_credentials() -> tuple[str | None, str | None]:
    """Resolve (token, chat_id) from env.

    Priority: TELEGRAM_LIVE_BOT_TOKEN/CHAT_ID (현 운영 표준 — 모든 알림 단일 채널)
        → TELEGRAM_QTA_BOT_TOKEN/CHAT_ID (#133 초기 표준)
        → legacy TELEGRAM_BOT_TOKEN/CHAT_ID.
    """
    token = (
        os.environ.get("TELEGRAM_LIVE_BOT_TOKEN")
        or os.environ.get("TELEGRAM_QTA_BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
    )
    chat_id = (
        os.environ.get("TELEGRAM_LIVE_CHAT_ID")
        or os.environ.get("TELEGRAM_QTA_CHAT_ID")
        or os.environ.get("TELEGRAM_CHAT_ID")
    )
    return token, chat_id


def send_telegram(text: str, *, parse_mode: str = "Markdown") -> bool:
    """Telegram bot sendMessage. token/chat_id 미설정 시 warn + skip (False 반환)."""
    token, chat_id = _resolve_telegram_credentials()
    if not token or not chat_id:
        log.warning(
            "TELEGRAM_LIVE_BOT_TOKEN/CHAT_ID "
            "(or TELEGRAM_QTA_*, legacy TELEGRAM_BOT_TOKEN/CHAT_ID) 미설정 — skip"
        )
        return False
    if len(text) > TELEGRAM_MAX_LEN:
        text = text[: TELEGRAM_MAX_LEN - 20] + "\n... (truncated)"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        log.error("Telegram request failed: %s", exc)
        return False
    if resp.status_code == 200:
        return True
    log.error("Telegram API %d: %s", resp.status_code, resp.text[:200])
    return False


def _format_position_stop(event: dict) -> str:
    """Friendly formatter for #227 LivePositionRiskManager exits."""
    p = event.get("payload") or event
    sid = p.get("strategy_id", "?")
    sym = p.get("symbol", "?")
    trigger = p.get("trigger", "?")
    avg_cost = p.get("avg_cost", "?")
    last_price = p.get("last_price", "?")
    pct = p.get("pct_change")
    icon = {"stop_loss": "🛑", "take_profit": "🎯", "trailing_stop": "📉"}.get(trigger, "⚠️")
    pct_str = f"{pct:+.2%}" if isinstance(pct, (int, float)) else "?"
    return (
        f"{icon} *{trigger}* `{sid}`\n"
        f"매도: `{sym}` @ {last_price} (매수가 {avg_cost}, {pct_str})"
    )


def is_critical_event(event: dict) -> tuple[bool, str]:
    """Returns (is_critical, formatted_message). Non-critical → (False, "")."""
    et = event.get("event_type") or ""
    if et == "position_stop_triggered":
        return True, _format_position_stop(event)
    if et in CRITICAL_EVENT_TYPES:
        payload = {k: v for k, v in event.items() if k != "event_type"}
        snippet = json.dumps(payload, ensure_ascii=False)[:300]
        return True, f"⚠️ *{et}*\n```\n{snippet}\n```"
    if et == "order_rejected":
        reason = str(event.get("reject_reason") or event.get("reason") or "")
        if reason.startswith("KILL_SWITCH"):
            return True, f"🛑 *kill_switch_tripped*\nreason: `{reason}`"
    return False, ""


def _find_latest_wal(log_dir: Path) -> Path | None:
    if not log_dir.exists():
        return None
    candidates = list(log_dir.rglob("wal.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def scan_new_lines(path: Path, cursor: dict[str, int]) -> list[dict]:
    """Read newly-appended lines from `path` since last cursor; advance cursor.

    Returns parsed JSON dicts (skips malformed lines).
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return []
    last = cursor.get(str(path), 0)
    if size <= last:
        return []
    with path.open("rb") as f:
        f.seek(last)
        data = f.read(size - last)
    cursor[str(path)] = size
    events: list[dict] = []
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return events


def watch_loop(log_dir: Path, *, poll_sec: float = 5.0) -> None:
    """Tail the most-recent wal.jsonl in log_dir; send Telegram on critical events."""
    log.info("watching %s (poll %ss)", log_dir, poll_sec)
    cursor: dict[str, int] = {}
    try:
        while True:
            wal = _find_latest_wal(log_dir)
            if wal is not None:
                for event in scan_new_lines(wal, cursor):
                    is_crit, msg = is_critical_event(event)
                    if is_crit:
                        send_telegram(msg)
            time.sleep(poll_sec)
    except KeyboardInterrupt:
        log.info("watch interrupted")


def summarize_report(text: str, name: str) -> str:
    """Markdown 리포트 → Telegram 4000 자 요약 (앞쪽 ~60줄 발췌)."""
    lines = text.splitlines()[:60]
    body = "\n".join(lines)
    return f"📊 *Daily Report — {name}*\n```\n{body}\n```"


def send_report_summary(report_path: Path) -> bool:
    if not report_path.exists():
        log.error("report file not found: %s", report_path)
        return False
    text = report_path.read_text(encoding="utf-8", errors="replace")
    return send_telegram(summarize_report(text, report_path.name))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QTA Phase 2 Telegram alert utility")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--watch", metavar="DIR", help="WAL polling mode")
    g.add_argument("--report", metavar="PATH", help="send daily report summary")
    g.add_argument("--test", action="store_true", help="send a test ping")
    parser.add_argument("--poll", type=float, default=5.0, help="watch polling interval (s)")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.test:
        return 0 if send_telegram("✅ QTA Phase 2 telegram_alert test ping") else 1
    if args.report:
        return 0 if send_report_summary(Path(args.report)) else 1
    if args.watch:
        watch_loop(Path(args.watch), poll_sec=args.poll)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

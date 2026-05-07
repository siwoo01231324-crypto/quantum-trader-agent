"""Fail-soft alert dispatcher: Slack and/or Telegram, stdout fallback."""
from __future__ import annotations

import os
import sys
from typing import Literal

import requests

Level = Literal["info", "warn", "critical"]

_LEVEL_EMOJI = {"info": "ℹ️", "warn": "⚠️", "critical": "🚨"}


def _build_text(level: Level, title: str, body: str, fields: dict | None) -> str:
    emoji = _LEVEL_EMOJI.get(level, "")
    parts = [f"{emoji} [{level.upper()}] {title}", body]
    if fields:
        parts += [f"• {k}: {v}" for k, v in fields.items()]
    return "\n".join(parts)


def _post(url: str, payload: dict) -> None:
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as exc:
        print(f"[alerts] WARNING: post failed ({exc})", file=sys.stderr)


def _resolve_telegram_env() -> tuple[str, str]:
    """Resolve (token, chat_id). Priority LIVE > QTA > legacy (#152)."""
    token = (
        os.environ.get("TELEGRAM_LIVE_BOT_TOKEN")
        or os.environ.get("TELEGRAM_QTA_BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
        or ""
    )
    chat_id = (
        os.environ.get("TELEGRAM_LIVE_CHAT_ID")
        or os.environ.get("TELEGRAM_QTA_CHAT_ID")
        or os.environ.get("TELEGRAM_CHAT_ID")
        or ""
    )
    return token, chat_id


def notify(
    level: Level,
    title: str,
    body: str,
    fields: dict | None = None,
) -> None:
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    tg_token, tg_chat = _resolve_telegram_env()

    text = _build_text(level, title, body, fields)
    sent = False

    if slack_url:
        _post(slack_url, {"text": text, "username": "metalabeler"})
        sent = True

    if tg_token and tg_chat:
        _post(
            f"https://api.telegram.org/bot{tg_token}/sendMessage",
            {"chat_id": tg_chat, "text": text},
        )
        sent = True

    if not sent:
        msg = f"[ALERT {level}] {title}\n {body}"
        # Windows consoles default to cp949/cp1252 and choke on emoji.
        # Encode/replace to whatever stdout supports.
        try:
            print(msg)
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "ascii"
            print(msg.encode(enc, errors="replace").decode(enc, errors="replace"))

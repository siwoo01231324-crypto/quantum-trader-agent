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


def notify(
    level: Level,
    title: str,
    body: str,
    fields: dict | None = None,
) -> None:
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")

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
        print(f"[ALERT {level}] {title}\n {body}")

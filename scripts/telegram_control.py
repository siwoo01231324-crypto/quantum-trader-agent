#!/usr/bin/env python3
"""Telegram 양방향 제어 봇 (Issue #126).

장외시간 / 외출 중에도 휴대폰으로 비상정지 발동/해제, 상태 조회를 가능하게 하는
single-user (single chat_id) 명령 봇. localhost FastAPI dashboard 의 REST API
를 호출해서 실행 — 별도 권한·상태 보유하지 않음 (대시보드와 동일한 접근권한).

Commands:
    /kill <reason>   비상정지 발동 — KillSwitch.trip()
    /release         비상정지 해제 (확인 메시지 응답 후 다음 /release 시 적용)
    /status          현재 PnL + 한도 사용률 + KillSwitch 상태
    /policy          정책 파일 (configs/policy.yaml) 요약
    /help            명령 목록

Usage:
    python scripts/telegram_control.py --dashboard http://localhost:8000

Env (.env, #216 fallback chain):
    TELEGRAM_LIVE_BOT_TOKEN / TELEGRAM_LIVE_CHAT_ID   1순위 (현 운영 표준)
    TELEGRAM_QTA_BOT_TOKEN  / TELEGRAM_QTA_CHAT_ID    2순위
    TELEGRAM_BOT_TOKEN      / TELEGRAM_CHAT_ID        legacy fallback
    chat_id 는 화이트리스트 (단일/복수 chat 허용, 콤마/공백 구분)
    TELEGRAM_AUDIT_WAL   선택 — `logs/shadow/.../wal.jsonl` 절대경로,
                         설정 시 모든 명령 수신을 WAL command_received 이벤트로 기록.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("telegram_control")

# Telegram API constants
LONG_POLL_TIMEOUT_S = 30
DEFAULT_POLL_INTERVAL_S = 1.0
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"

# Dashboard endpoint mapping (#125 / #198)
ENDPOINT_KILL_TRIGGER = "/api/kill-switch/trigger"
ENDPOINT_KILL_RESET = "/api/kill-switch/reset"
ENDPOINT_KILL_STATE = "/api/kill-switch"
ENDPOINT_PNL = "/api/pnl"
ENDPOINT_LIMITS = "/api/limits"


@dataclass(frozen=True)
class BotConfig:
    """Runtime config for the Telegram control bot."""

    token: str
    allowed_chat_ids: frozenset[int]
    dashboard_base_url: str
    audit_wal_path: Path | None
    policy_yaml_path: Path | None


@dataclass
class CommandResult:
    """Outcome of a single command — used for audit log + reply."""

    command: str
    args: str
    chat_id: int
    user_id: int | None
    accepted: bool
    reply: str
    reason: str | None = None  # rejection reason if accepted=False


# ---------------------------------------------------------------------------
# Audit logging — WAL append via plain JSONL write (no broker dep)
# ---------------------------------------------------------------------------

def write_audit_event(wal_path: Path, result: CommandResult) -> None:
    """Append a `command_received` event to the WAL JSONL.

    Best-effort: any I/O failure logs a warning but never raises (the bot must
    keep running even if the WAL filesystem is unavailable).
    """
    try:
        wal_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "schema_version": 1,
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": "command_received",
            "payload": {
                "command": result.command,
                "args": result.args,
                "chat_id": result.chat_id,
                "user_id": result.user_id,
                "accepted": result.accepted,
                "reason": result.reason,
                "source": "telegram_control",
            },
        }
        with open(wal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — audit log must not crash bot
        log.warning("audit log write failed: %s", exc)


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def telegram_get_updates(token: str, offset: int) -> list[dict]:
    """Long-poll Telegram getUpdates. Returns list of update dicts (may be empty)."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"offset": offset, "timeout": LONG_POLL_TIMEOUT_S}
    try:
        resp = requests.get(url, params=params, timeout=LONG_POLL_TIMEOUT_S + 5)
    except requests.RequestException as exc:
        log.warning("getUpdates request failed: %s", exc)
        return []
    if resp.status_code != 200:
        log.warning("getUpdates HTTP %d: %s", resp.status_code, resp.text[:200])
        return []
    body = resp.json()
    if not body.get("ok"):
        log.warning("getUpdates not ok: %s", body)
        return []
    return body.get("result", [])


def telegram_send_message(token: str, chat_id: int, text: str) -> bool:
    """Telegram sendMessage. Returns True on 200."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    if len(text) > 4000:
        text = text[:3996] + "\n..."
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
    except requests.RequestException as exc:
        log.warning("sendMessage failed: %s", exc)
        return False
    return resp.status_code == 200


# ---------------------------------------------------------------------------
# Command parsing + dispatch
# ---------------------------------------------------------------------------

def parse_command(text: str) -> tuple[str, str]:
    """Split `/cmd args` -> (cmd, args). Returns ('', '') for non-command text."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return "", ""
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/")
    # Telegram appends @BotName to commands sent in groups — strip it.
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]
    args = parts[1] if len(parts) > 1 else ""
    return cmd, args


def is_allowed_chat(config: BotConfig, chat_id: int) -> bool:
    """chat_id whitelist check. Empty whitelist denies everyone."""
    return chat_id in config.allowed_chat_ids


# Pending /release confirmation state (single-bot scope, not persisted).
# Maps chat_id -> ts of the most recent /release request awaiting confirmation.
_pending_release: dict[int, float] = {}
RELEASE_CONFIRM_WINDOW_S = 60.0


def reset_pending_release_for_test() -> None:
    """Test helper — clear in-process state."""
    _pending_release.clear()


def handle_kill(config: BotConfig, args: str, chat_id: int, user_id: int | None) -> CommandResult:
    """`/kill <reason>` — POST kill-switch trigger."""
    reason = args.strip() or "telegram_manual"
    url = f"{config.dashboard_base_url.rstrip('/')}{ENDPOINT_KILL_TRIGGER}"
    try:
        resp = requests.post(url, json={"reason": "manual"}, timeout=10)
    except requests.RequestException as exc:
        return CommandResult(
            command="kill", args=reason, chat_id=chat_id, user_id=user_id,
            accepted=False, reply=f"❌ dashboard unreachable: {exc}",
            reason="dashboard_unreachable",
        )
    if resp.status_code != 200:
        return CommandResult(
            command="kill", args=reason, chat_id=chat_id, user_id=user_id,
            accepted=False, reply=f"❌ dashboard returned HTTP {resp.status_code}",
            reason=f"http_{resp.status_code}",
        )
    return CommandResult(
        command="kill", args=reason, chat_id=chat_id, user_id=user_id,
        accepted=True,
        reply=f"🔴 비상정지 발동\nreason: {reason}\n시각: {datetime.now(timezone.utc).isoformat()}",
    )


def handle_release(config: BotConfig, args: str, chat_id: int, user_id: int | None) -> CommandResult:
    """`/release` — 2-step confirmation. First call asks for confirm; second within
    60s actually resets the kill-switch."""
    now = time.time()
    pending_ts = _pending_release.get(chat_id)

    if pending_ts is None or (now - pending_ts) > RELEASE_CONFIRM_WINDOW_S:
        # First press — store pending state and ask for confirm.
        _pending_release[chat_id] = now
        return CommandResult(
            command="release", args=args, chat_id=chat_id, user_id=user_id,
            accepted=False,
            reply=(
                "⚠️ 비상정지 해제 확인\n"
                "60초 안에 `/release` 한 번 더 보내면 해제됩니다."
            ),
            reason="awaiting_confirmation",
        )

    # Second press within window — execute reset.
    _pending_release.pop(chat_id, None)
    url = f"{config.dashboard_base_url.rstrip('/')}{ENDPOINT_KILL_RESET}"
    try:
        resp = requests.post(url, json={"reason": "manual"}, timeout=10)
    except requests.RequestException as exc:
        return CommandResult(
            command="release", args=args, chat_id=chat_id, user_id=user_id,
            accepted=False, reply=f"❌ dashboard unreachable: {exc}",
            reason="dashboard_unreachable",
        )
    if resp.status_code != 200:
        return CommandResult(
            command="release", args=args, chat_id=chat_id, user_id=user_id,
            accepted=False, reply=f"❌ dashboard returned HTTP {resp.status_code}",
            reason=f"http_{resp.status_code}",
        )
    return CommandResult(
        command="release", args=args, chat_id=chat_id, user_id=user_id,
        accepted=True,
        reply=f"🟢 비상정지 해제\n시각: {datetime.now(timezone.utc).isoformat()}",
    )


def handle_status(config: BotConfig, args: str, chat_id: int, user_id: int | None) -> CommandResult:
    """`/status` — fetch PnL + limits + kill-switch state."""
    base = config.dashboard_base_url.rstrip("/")
    out: dict[str, Any] = {}
    try:
        for key, path in (("pnl", ENDPOINT_PNL), ("limits", ENDPOINT_LIMITS), ("ks", ENDPOINT_KILL_STATE)):
            r = requests.get(f"{base}{path}", timeout=10)
            if r.status_code == 200:
                out[key] = r.json()
    except requests.RequestException as exc:
        return CommandResult(
            command="status", args=args, chat_id=chat_id, user_id=user_id,
            accepted=False, reply=f"❌ dashboard unreachable: {exc}",
            reason="dashboard_unreachable",
        )

    if not out:
        return CommandResult(
            command="status", args=args, chat_id=chat_id, user_id=user_id,
            accepted=False, reply="❌ no data from dashboard",
            reason="empty_response",
        )

    lines = ["📊 Status"]
    pnl = out.get("pnl", {})
    if pnl:
        lines.append(
            f"PnL: realtime={pnl.get('realtime', 0):.2f} "
            f"daily={pnl.get('daily', 0):.2f} "
            f"monthly={pnl.get('monthly', 0):.2f}"
        )
    limits = out.get("limits", {})
    if limits:
        worst_label, worst_val = max(limits.items(), key=lambda kv: kv[1] or 0)
        lines.append(f"Limits worst: {worst_label}={worst_val * 100:.1f}%")
    ks = out.get("ks", {})
    if ks:
        active = [k for k, v in (ks.get("triggers") or {}).items() if v]
        lines.append(
            f"KillSwitch: {'🔴 ACTIVE (' + ','.join(active) + ')' if active else '🟢 normal'}"
        )

    return CommandResult(
        command="status", args=args, chat_id=chat_id, user_id=user_id,
        accepted=True, reply="\n".join(lines),
    )


def handle_policy(config: BotConfig, args: str, chat_id: int, user_id: int | None) -> CommandResult:
    """`/policy` — summarize policy.yaml top-level keys."""
    if config.policy_yaml_path is None or not config.policy_yaml_path.exists():
        return CommandResult(
            command="policy", args=args, chat_id=chat_id, user_id=user_id,
            accepted=False, reply="❌ policy.yaml not configured or missing",
            reason="policy_unavailable",
        )
    try:
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(config.policy_yaml_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        return CommandResult(
            command="policy", args=args, chat_id=chat_id, user_id=user_id,
            accepted=False, reply=f"❌ policy parse failed: {exc}",
            reason="parse_error",
        )

    lines = ["📋 Policy summary"]
    for key in sorted(data.keys()):
        val = data[key]
        if isinstance(val, dict):
            lines.append(f"{key}: ({len(val)} keys)")
        elif isinstance(val, list):
            lines.append(f"{key}: ({len(val)} items)")
        else:
            preview = str(val)
            if len(preview) > 60:
                preview = preview[:57] + "..."
            lines.append(f"{key}: {preview}")
    return CommandResult(
        command="policy", args=args, chat_id=chat_id, user_id=user_id,
        accepted=True, reply="\n".join(lines),
    )


def handle_help(_config: BotConfig, args: str, chat_id: int, user_id: int | None) -> CommandResult:
    reply = (
        "QTA 제어봇 명령\n"
        "/kill <reason>  비상정지 발동\n"
        "/release        비상정지 해제 (60초내 두 번)\n"
        "/status         PnL + 한도 + KillSwitch\n"
        "/policy         정책 요약\n"
        "/help           이 메시지"
    )
    return CommandResult(
        command="help", args=args, chat_id=chat_id, user_id=user_id,
        accepted=True, reply=reply,
    )


_HANDLERS = {
    "kill": handle_kill,
    "release": handle_release,
    "status": handle_status,
    "policy": handle_policy,
    "help": handle_help,
    "start": handle_help,  # /start = /help on first interaction
}


def dispatch(config: BotConfig, message: dict) -> CommandResult | None:
    """Parse + auth-check + dispatch one Telegram message. Returns None for non-commands."""
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    user = message.get("from") or {}
    user_id = user.get("id")
    text = message.get("text") or ""

    cmd, args = parse_command(text)
    if not cmd:
        return None

    if not is_allowed_chat(config, chat_id):
        return CommandResult(
            command=cmd, args=args, chat_id=chat_id or 0, user_id=user_id,
            accepted=False, reply="🚫 unauthorized chat_id",
            reason="unauthorized",
        )

    handler = _HANDLERS.get(cmd)
    if handler is None:
        return CommandResult(
            command=cmd, args=args, chat_id=chat_id, user_id=user_id,
            accepted=False, reply=f"❓ unknown command: /{cmd}\n/help 로 확인하세요.",
            reason="unknown_command",
        )

    return handler(config, args, chat_id, user_id)


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------

def poll_once(config: BotConfig, offset: int) -> tuple[int, list[CommandResult]]:
    """One getUpdates round. Returns (new_offset, [results])."""
    updates = telegram_get_updates(config.token, offset)
    results: list[CommandResult] = []
    new_offset = offset
    for upd in updates:
        new_offset = max(new_offset, int(upd.get("update_id", 0)) + 1)
        message = upd.get("message") or upd.get("edited_message")
        if not message:
            continue
        result = dispatch(config, message)
        if result is None:
            continue
        results.append(result)
        # Send reply
        telegram_send_message(config.token, result.chat_id, result.reply)
        # Audit log
        if config.audit_wal_path is not None:
            write_audit_event(config.audit_wal_path, result)
    return new_offset, results


def run_loop(config: BotConfig, *, max_iterations: int | None = None) -> int:
    """Main poll loop. `max_iterations` for tests."""
    if not config.token or not config.allowed_chat_ids:
        log.error("missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID — exiting")
        return 1
    log.info(
        "telegram_control start — dashboard=%s allowed_chats=%d audit_wal=%s",
        config.dashboard_base_url, len(config.allowed_chat_ids),
        config.audit_wal_path,
    )
    offset = 0
    iteration = 0
    while True:
        if max_iterations is not None and iteration >= max_iterations:
            return 0
        try:
            offset, _ = poll_once(config, offset)
        except KeyboardInterrupt:
            log.info("interrupted by user")
            return 130
        except Exception as exc:  # noqa: BLE001 — keep loop alive
            log.exception("poll iteration failed: %s", exc)
            time.sleep(5.0)
        iteration += 1
    return 0  # pragma: no cover


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram bidirectional control bot (#126)")
    parser.add_argument("--dashboard", default="http://localhost:8000",
                        help="Dashboard base URL (default http://localhost:8000)")
    parser.add_argument("--audit-wal", default=None,
                        help="WAL JSONL path for command audit log (default: TELEGRAM_AUDIT_WAL env)")
    parser.add_argument("--policy", default="configs/policy.yaml",
                        help="Policy YAML path for /policy command (default configs/policy.yaml)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def build_config_from_env(args: argparse.Namespace) -> BotConfig:
    # #216 fallback chain — 시우님 .env 가 TELEGRAM_LIVE_*/TELEGRAM_QTA_* 만 보유.
    # 우선순위: TELEGRAM_LIVE_* (운영 표준) > TELEGRAM_QTA_* (#133 초기) > legacy
    # TELEGRAM_BOT_TOKEN/CHAT_ID. telegram_alert.py 와 동일 패턴 (동일 LIVE 봇 채널).
    token = (
        os.environ.get("TELEGRAM_LIVE_BOT_TOKEN")
        or os.environ.get("TELEGRAM_QTA_BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    )
    chat_id_raw = (
        os.environ.get("TELEGRAM_LIVE_CHAT_ID")
        or os.environ.get("TELEGRAM_QTA_CHAT_ID")
        or os.environ.get("TELEGRAM_CHAT_ID", "")
    )
    chat_ids: set[int] = set()
    for tok in chat_id_raw.replace(",", " ").split():
        try:
            chat_ids.add(int(tok))
        except ValueError:
            log.warning("invalid chat_id token: %r — skipped", tok)

    audit_wal = args.audit_wal or os.environ.get("TELEGRAM_AUDIT_WAL")
    audit_path = Path(audit_wal) if audit_wal else None

    policy_path = Path(args.policy) if args.policy else None
    if policy_path and not policy_path.is_absolute():
        policy_path = Path.cwd() / policy_path

    return BotConfig(
        token=token,
        allowed_chat_ids=frozenset(chat_ids),
        dashboard_base_url=args.dashboard,
        audit_wal_path=audit_path,
        policy_yaml_path=policy_path,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = build_config_from_env(args)
    return run_loop(config)


if __name__ == "__main__":
    sys.exit(main())

"""docs/journal_data/<date>.json one-off backfill for forgotten exports.

`src/dashboard/app.py::_build_journal_today()` 와 동일 로직이지만 KST 윈도우를
"now" 가 아니라 *지정 날짜* 로 고정한다. 사용자가 23:50 KST 의 dashboard
"오늘 journal git push" 버튼 클릭을 깜빡한 날을 사후 백필 할 때 쓴다.

Usage:
    python scripts/export_journal_for_date.py 2026-05-26

Limits:
- ``cs_tsmom_top10`` 은 시점 스냅샷이라 사후 재구성 불가 → ``available: false``
  + note 로 마킹. 모델 score 는 daily close 기반이라 *현재* 호출로도 거의
  동등하지만, "그날 23:50 KST 의 상태" 보장이 안 되므로 보수적으로 null.
- airborne fires 는 ``docker logs --since X --until Y`` 로 컨테이너 잔존 로그
  범위 안에서만 복구 (docker 가 log rotation 했으면 누락).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.dashboard.app import _parse_airborne_fire_line  # noqa: E402
from src.live.trade_history import discover_wal_files  # noqa: E402
from src.live.wal import replay as wal_replay  # noqa: E402

KST = ZoneInfo("Asia/Seoul")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date_kst", help="YYYY-MM-DD (KST)")
    ap.add_argument("--log-dir", default=str(REPO_ROOT / "logs" / "live"))
    ap.add_argument("--container", default="qta-airborne-daemon",
                    help="Docker container with airborne FIRE logs")
    ap.add_argument("--out", default=None,
                    help="Override output path (default: docs/journal_data/<date>.json)")
    args = ap.parse_args()

    target = datetime.strptime(args.date_kst, "%Y-%m-%d").replace(tzinfo=KST)
    kst_start = target
    kst_end_excl = target + timedelta(days=1)
    utc_start = kst_start.astimezone(timezone.utc)
    utc_end_excl = kst_end_excl.astimezone(timezone.utc)

    def _in_window(iso: str) -> bool:
        try:
            t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return False
        return utc_start <= t < utc_end_excl

    # ── WAL events (auto_fills + auto_signals) ──
    log_dir = Path(args.log_dir)
    auto_fills: list[dict] = []
    auto_signals: list[dict] = []
    wal_paths: list[Path] = []
    if log_dir.is_dir():
        wal_paths = list(discover_wal_files(log_dir))
        for p in wal_paths:
            events, _ = wal_replay(p)
            for ev in events:
                if not _in_window(ev.ts):
                    continue
                pl = ev.payload or {}
                if ev.event_type in ("order_filled", "fill_received"):
                    auto_fills.append({"ts": ev.ts, **pl})
                elif ev.event_type == "signal_emitted":
                    auto_signals.append({"ts": ev.ts, **pl})

    # ── manual trades ──
    manual: list[dict] = []
    mpath = log_dir / "manual_trade.jsonl"
    if mpath.exists():
        for line in mpath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not _in_window(rec.get("ts", "")):
                continue
            pl = rec.get("payload") or {}
            manual.append({"ts": rec.get("ts"), **pl})

    # ── airborne FIRE via docker logs --since/--until ──
    since_iso = utc_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    until_iso = utc_end_excl.strftime("%Y-%m-%dT%H:%M:%SZ")
    airborne_fires: list[dict] = []
    docker_err: str | None = None
    try:
        cp = subprocess.run(
            ["docker", "logs", args.container,
             "--since", since_iso, "--until", until_iso],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        if cp.returncode == 0:
            for line in ((cp.stdout or "") + (cp.stderr or "")).splitlines():
                rec = _parse_airborne_fire_line(line)
                if rec is not None and _in_window(rec.get("ts", "")):
                    airborne_fires.append(rec)
        else:
            docker_err = f"docker logs returned {cp.returncode}: {cp.stderr[:200]}"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        docker_err = f"{type(exc).__name__}: {exc}"

    # ── cs_tsmom_top10 — historical snapshot missing ──
    cs_tsmom_top10 = {
        "available": False,
        "fetched_at": None,
        "pin_date": "",
        "top10": [],
        "note": (
            f"Backfill at {datetime.now(KST).isoformat()} — historical snapshot "
            f"for {args.date_kst} 23:50 KST was not captured. Routine 가 'today "
            f"TOP10' 으로 쓰면 부정확할 수 있음. auto_fills + auto_signals 를 "
            f"primary source 로 활용."
        ),
    }

    # ── 잔고 검산 (bill 원장) — 로컬 .env creds 로 Bitget read-only fetch ──
    # 클라우드 routine 은 API 접근 불가하므로 *이 로컬 export* 가 JSON 에 굳혀둔다.
    # 실패해도 export 전체를 막지 않는다 (graceful).
    try:
        from scripts.bitget_account_reconcile import reconcile as _reconcile
        account_reconciliation = _reconcile(args.date_kst)
    except Exception as exc:  # noqa: BLE001 — 검산은 보조, export 차단 금지
        account_reconciliation = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    for arr in (auto_fills, auto_signals, manual):
        arr.sort(key=lambda r: str(r.get("ts") or ""))

    payload = {
        "date_kst": args.date_kst,
        "kst_window_start": kst_start.isoformat(),
        "now_kst": datetime.now(KST).isoformat(),
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
        "account_reconciliation": account_reconciliation,
        "_backfill_meta": {
            "generated_by": "scripts/export_journal_for_date.py",
            "generated_at_kst": datetime.now(KST).isoformat(),
            "utc_window": [utc_start.isoformat(), utc_end_excl.isoformat()],
            "wal_files_scanned": [str(p) for p in wal_paths],
            "docker_error": docker_err,
        },
    }

    out = Path(args.out) if args.out else (
        REPO_ROOT / "docs" / "journal_data" / f"{args.date_kst}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"Wrote {out}  ("
        f"auto_fills={len(auto_fills)}, manual={len(manual)}, "
        f"signals={len(auto_signals)}, airborne={len(airborne_fires)}"
        + (f", docker_error={docker_err}" if docker_err else "")
        + ")"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

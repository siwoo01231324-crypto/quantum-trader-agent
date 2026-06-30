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
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.dashboard.airborne_fire_store import AirborneFireStore  # noqa: E402
from src.dashboard.app import (  # noqa: E402
    _parse_airborne_fires_from_docker_logs,
)
from src.live.trade_history import discover_wal_files  # noqa: E402
from src.live.wal import replay as wal_replay  # noqa: E402

KST = ZoneInfo("Asia/Seoul")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date_kst", help="YYYY-MM-DD (KST)")
    # 2026-06-22: logs/live (5월 이후 죽은 디렉토리) → logs/shadow-bitget.
    # 실거래 트레이더(호스트 live_run.py)의 WAL 기본 경로가 logs/shadow-bitget/
    # {run_id}/wal.jsonl 이라, 기존 logs/live 스캔은 auto_fills=0 만 내놨다
    # (0619~0621 리포트 3일 연속 갭의 원인). live_run.py 의 --log-dir 기본값과 일치.
    ap.add_argument("--log-dir", default=str(REPO_ROOT / "logs" / "shadow-bitget"))
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
    # 2026-06-30: 스윙(터틀) 전환 — 스윙 WAL 은 logs/shadow-swing(/shadow-swing-binance)
    # 에 떨어진다(대시보드 스윙버튼 --log-dir, #506). 에어본 시절 logs/shadow-bitget
    # 와 함께 *둘 다* 스캔해 auto_fills/auto_signals 가 swing 전략(live-capitulation-
    # bounce / live-donchian-breakout-btcgate) 체결·신호를 포착하게 한다. ledger
    # (auto_pnl_ledger)는 전략무관이라 PnL 은 어차피 정확하지만, 신호/사유 대조엔 WAL 필요.
    log_dir = Path(args.log_dir)
    _wal_dirs = [log_dir,
                 REPO_ROOT / "logs" / "shadow-swing",
                 REPO_ROOT / "logs" / "shadow-swing-binance"]
    auto_fills: list[dict] = []
    auto_signals: list[dict] = []
    wal_paths: list[Path] = []
    for _d in _wal_dirs:
        if _d.is_dir():
            wal_paths.extend(discover_wal_files(_d))
    if wal_paths:
        for p in sorted(set(wal_paths)):
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

    # ── airborne FIRE — 영속 store(history.jsonl) 가 단일 진실 ──
    # 과거: docker logs --since/--until 만 읽어, 컨테이너 재생성/rotation 시
    # 그 인스턴스 이후 fire 만 잡혀 "저녁 100% 집중" 같은 잘림 착시 발생
    # (qta-airborne-daemon 06-24 19:53 KST 재생성 → 종일 176건 중 106건만 포착,
    #  새벽~오후 100건 유실). dashboard 가 store 에 연속 append 해 rotation 을
    # 견디므로, store 를 primary 로 읽고 docker logs 는 store 가 아직 못 본
    # fire 만 best-effort backfill 하는 보조 소스로 강등.
    store = AirborneFireStore(
        str(REPO_ROOT / "logs" / "airborne_fires" / "history.jsonl"),
    )
    docker_err: str | None = None
    docker_backfilled = 0
    try:
        since_4d = (utc_start - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        live_fires = _parse_airborne_fires_from_docker_logs(
            since_4d, container_name=args.container,
        )
        if live_fires:
            docker_backfilled = store.append_many(live_fires)
    except Exception as exc:  # noqa: BLE001 — backfill 은 보조, export 차단 금지
        docker_err = f"{type(exc).__name__}: {exc}"

    airborne_fires = [
        f for f in store.load_since(utc_start) if _in_window(f.get("ts", ""))
    ]
    airborne_fires.sort(key=lambda r: str(r.get("ts") or ""))
    store_count = store.count()
    store_earliest = store.earliest_ts()

    # ── airborne 적중 sim — 영속 cache(sim_cache.jsonl) 재사용, klines fetch 없음 ──
    # dashboard 가 Binance 봉으로 미리 계산해둔 outcome/pct 를 읽어 집계만 한다.
    # cache miss(아직 sim 안 된) fire 는 skip → klines 차단 환경에서도 graceful.
    # 이전엔 이 환경에서 TP/SL/net 이 통째로 N/A 였던 칸을 cache 로 채운다.
    try:
        from src.dashboard.airborne_sim_cache import AirborneSimCache
        from src.dashboard.app import _aggregate_airborne_sims
        _sim_cache = AirborneSimCache(
            str(REPO_ROOT / "logs" / "airborne_fires" / "sim_cache.jsonl"),
        )
        cached_sims, missing_sims = _sim_cache.split(airborne_fires)
        airborne_sim: dict = {
            "ok": True,
            "rule": "TP +1.0% / SL -0.5% / 4bar(1h) hold, fee 0.034%",
            "source": "airborne_sim_cache(sim_cache.jsonl)",
            "fires_total": len(airborne_fires),
            "fires_simulated": len(cached_sims),
            "fires_uncached": len(missing_sims),
            **_aggregate_airborne_sims(cached_sims),
        }
    except Exception as exc:  # noqa: BLE001 — 보조, export 차단 금지
        airborne_sim = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

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

    # ── 일일손익 (auto_pnl_ledger) — history-position netProfit (규칙 4 단일 진실) ──
    # 클라우드 routine 은 Bitget API 불가 → 이 로컬 export 가 JSON 에 굳혀둔다.
    # WAL auto_fills 가 0(경로 갭)이어도 일일손익은 이 필드로 정확히 채워진다.
    try:
        from scripts.bitget_account_reconcile import fetch_position_history_pnl
        auto_pnl_ledger = fetch_position_history_pnl(args.date_kst)
    except Exception as exc:  # noqa: BLE001 — 보조, export 차단 금지
        auto_pnl_ledger = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

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
        "airborne_sim": airborne_sim,
        "cs_tsmom_top10": cs_tsmom_top10,
        "account_reconciliation": account_reconciliation,
        "auto_pnl_ledger": auto_pnl_ledger,
        "_backfill_meta": {
            "generated_by": "scripts/export_journal_for_date.py",
            "generated_at_kst": datetime.now(KST).isoformat(),
            "utc_window": [utc_start.isoformat(), utc_end_excl.isoformat()],
            "wal_files_scanned": [str(p) for p in wal_paths],
            "docker_error": docker_err,
            "airborne_source": "airborne_fire_store(history.jsonl)",
            "airborne_store_count": store_count,
            "airborne_store_earliest": store_earliest,
            "airborne_docker_backfilled": docker_backfilled,
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

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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

from src.dashboard.timeline_broker import TimelineBroker
from src.live.wal import replay as wal_replay
from src.observability.metrics import Metrics


@dataclass
class DashboardState:
    """Mutable runtime state shared across request handlers."""

    # 손익
    pnl_realtime: float = 0.0
    pnl_daily: float = 0.0
    pnl_monthly: float = 0.0

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


def _gauge_html(name: str, value: float) -> str:
    pct = min(max(value * 100, 0), 100)
    color = "#e74c3c" if pct >= 80 else "#f39c12" if pct >= 60 else "#2ecc71"
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
    return f'<tr><td>{ts}</td><td><span class="tl-badge {type_class}">{typ}</span></td><td>{detail}</td></tr>'


def _kill_row(name: str, active: bool) -> str:
    status_cls = "ks-active" if active else "ks-normal"
    status_txt = "발동" if active else "정상"
    return f"""
    <tr>
      <td>{html.escape(name)}</td>
      <td><span class="{status_cls}">{status_txt}</span></td>
    </tr>"""


def _render_dashboard(state: DashboardState) -> str:
    # Q1: 손익
    pnl_realtime_fmt = f"{state.pnl_realtime:,.2f}"
    pnl_daily_fmt = f"{state.pnl_daily:,.2f}"
    pnl_monthly_fmt = f"{state.pnl_monthly:,.2f}"

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
    ks_overall_cls = "ks-active" if any_active else "ks-normal"
    ks_overall_txt = "비상정지 발동 중" if any_active else "정상 운영"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="5">
<title>QTA Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#0f1117;color:#e0e0e0;padding:12px}}
h1{{font-size:1.1rem;color:#7ecef4;margin-bottom:10px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;grid-template-rows:auto auto;gap:10px}}
.card{{background:#1a1d27;border:1px solid #2a2d3a;border-radius:8px;padding:14px}}
.card h2{{font-size:.85rem;color:#aaa;text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px}}
.pnl-row{{display:flex;gap:20px;flex-wrap:wrap}}
.pnl-item{{background:#0f1117;border-radius:6px;padding:8px 12px;min-width:120px}}
.pnl-label{{font-size:.7rem;color:#888}}
.pnl-value{{font-size:1.2rem;font-weight:700;color:#2ecc71}}
.gauge-row{{display:flex;align-items:center;gap:8px;margin-bottom:6px}}
.gauge-label{{width:110px;font-size:.75rem;color:#bbb;flex-shrink:0}}
.gauge-bar-bg{{flex:1;height:14px;background:#2a2d3a;border-radius:7px;overflow:hidden}}
.gauge-bar{{height:100%;border-radius:7px;transition:width .3s}}
.gauge-pct{{width:40px;font-size:.75rem;text-align:right;color:#ddd}}
table{{width:100%;border-collapse:collapse;font-size:.78rem}}
th,td{{padding:5px 8px;text-align:left;border-bottom:1px solid #2a2d3a}}
th{{color:#888;font-weight:600}}
.tl-badge{{border-radius:3px;padding:1px 5px;font-size:.7rem;font-weight:700}}
.tl-signal{{background:#1a3a5c;color:#7ecef4}}
.tl-meta{{background:#2a1a5c;color:#b07ef4}}
.tl-order{{background:#2a3a1a;color:#7ef47e}}
.tl-fill{{background:#3a2a1a;color:#f4b07e}}
.ks-active{{color:#e74c3c;font-weight:700}}
.ks-normal{{color:#2ecc71}}
.ks-overall{{font-size:1rem;font-weight:700;margin-bottom:8px}}
.btn{{border:none;border-radius:4px;padding:6px 14px;cursor:pointer;font-size:.8rem;margin:3px}}
.btn-trigger{{background:#c0392b;color:#fff}}
.btn-reset{{background:#27ae60;color:#fff}}
.btn-trigger:hover{{background:#e74c3c}}
.btn-reset:hover{{background:#2ecc71}}
.ks-controls{{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap}}
.last-ts{{font-size:.72rem;color:#888;margin-top:6px}}
</style>
</head>
<body>
<h1>QTA 로컬 대시보드 — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</h1>
<div class="grid">

  <!-- Q1: 손익 그래프 -->
  <div class="card">
    <h2>손익 (PnL)</h2>
    <div class="pnl-row">
      <div class="pnl-item">
        <div class="pnl-label">실시간</div>
        <div class="pnl-value">{pnl_realtime_fmt}</div>
      </div>
      <div class="pnl-item">
        <div class="pnl-label">일간</div>
        <div class="pnl-value">{pnl_daily_fmt}</div>
      </div>
      <div class="pnl-item">
        <div class="pnl-label">월간</div>
        <div class="pnl-value">{pnl_monthly_fmt}</div>
      </div>
    </div>
  </div>

  <!-- Q2: 한도 사용률 게이지 -->
  <div class="card">
    <h2>한도 사용률 (6종)</h2>
    {gauges_html}
  </div>

  <!-- Q3: 타임라인 -->
  <div class="card">
    <h2>신호 → 메타라벨러 → 주문 → 체결 타임라인</h2>
    <table>
      <thead><tr><th>시각</th><th>유형</th><th>상세</th></tr></thead>
      <tbody id="timeline">{rows_html}</tbody>
    </table>
  </div>

  <!-- Q4: 비상정지 -->
  <div class="card">
    <h2>비상정지 상태</h2>
    <div class="ks-overall {ks_overall_cls}">{ks_overall_txt}</div>
    <table>
      <thead><tr><th>트리거</th><th>상태</th></tr></thead>
      <tbody>{ks_rows}</tbody>
    </table>
    <div class="last-ts">마지막 발동: {last_ts}</div>
    <div class="ks-controls">
      <button class="btn btn-trigger" onclick="triggerKS('manual')">수동 발동 (trigger)</button>
      <button class="btn btn-reset" onclick="resetKS('manual')">수동 해제 (reset)</button>
    </div>
  </div>

</div>
<script>
async function triggerKS(reason){{
  await fetch('/api/kill-switch/trigger',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{reason}})}});
  location.reload();
}}
async function resetKS(reason){{
  await fetch('/api/kill-switch/reset',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{reason}})}});
  location.reload();
}}

// /ws/timeline live + replay (#181). 메타 새로고침과 충돌하지만 5초 사이 incremental update 가능.
const TYPE_CLASS = {{
  signal_emitted: 'tl-signal',
  metalabeler_decision: 'tl-meta',
  order_placed: 'tl-order',
  fill_received: 'tl-fill',
}};
const TIMELINE_MAX_ROWS = 100;
function tlEscape(s){{
  return String(s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
}}
function tlAppend(ev){{
  const tbody = document.getElementById('timeline');
  if(!tbody) return;
  if(ev.phase === 'live_ready') return;
  const cls = TYPE_CLASS[ev.event_type] || '';
  const detail = ev.payload ? tlEscape(JSON.stringify(ev.payload)) : '';
  const row = document.createElement('tr');
  row.innerHTML = `<td>${{tlEscape(ev.ts || '')}}</td><td><span class="tl-badge ${{cls}}">${{tlEscape(ev.event_type || '')}}</span></td><td>${{detail}}</td>`;
  tbody.insertBefore(row, tbody.firstChild);
  while(tbody.rows.length > TIMELINE_MAX_ROWS){{
    tbody.deleteRow(tbody.rows.length - 1);
  }}
}}
function tlConnect(){{
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${{proto}}//${{location.host}}/ws/timeline?replay=${{TIMELINE_MAX_ROWS}}`);
  ws.onmessage = (e) => {{
    try {{ tlAppend(JSON.parse(e.data)); }} catch(err) {{ console.warn('ws parse', err); }}
  }};
  ws.onclose = () => setTimeout(tlConnect, 1000);
  ws.onerror = () => ws.close();
}}
if (typeof WebSocket !== 'undefined') {{ tlConnect(); }}
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

    app = FastAPI(title="QTA Dashboard", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        return HTMLResponse(content=_render_dashboard(state))

    @app.get("/metrics")
    async def metrics() -> Response:
        data = generate_latest(state.metrics.registry)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/pnl")
    async def api_pnl() -> JSONResponse:
        return JSONResponse({
            "realtime": state.pnl_realtime,
            "daily": state.pnl_daily,
            "monthly": state.pnl_monthly,
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

    return app


# Standalone entry point: uvicorn src.dashboard.app:app
app = create_app()

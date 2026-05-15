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
from typing import Any, Callable
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

from src.dashboard.ops_counters import OpsCounters
from src.dashboard.shadow_runs import discover_shadow_runs, load_run_detail
from src.dashboard.strategy_catalog import load_production_status, load_strategy_catalog
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

    # 전략 카탈로그 + 토글 (#178 + #180)
    orchestrator: Any | None = None  # AsyncStrategyOrchestrator (avoid import cycle)
    specs_dir: Path | None = None    # docs/specs/strategies — fall back to repo default
    position_provider: Callable[[str], list[tuple[str, float]]] | None = None

    # 거래 시작/정지 컨트롤 (#182 단계 2). dashboard-only 모드에서만 주입.
    run_controller: object | None = None

    # KIS + Binance 계좌 정보 provider (#182 — "내 계좌" 카드)
    account_info_provider: object | None = None

    # Shadow Runs 뷰어 (#198) — logs/shadow/{run_id}/wal.jsonl 디렉토리 루트
    shadow_log_dir: Path | None = None

    # 라이브 PnL aggregator (#194). When wired, /api/pnl and per-card
    # pnl_today are sourced from this object instead of the legacy
    # pnl_realtime / pnl_daily / pnl_monthly fields above (kept for
    # backwards compatibility with callers that set them directly).
    pnl_aggregator: Any | None = None

    # Operational diagnostics — bars seen, dispatch counts, last signal/order/fill.
    # Same WAL observer that feeds pnl/timeline updates these counters.
    ops_counters: OpsCounters | None = None

    # Multi-broker smoke runs (`smoke-dual`) write to a primary `wal_path`
    # (KIS) and an auxiliary log under `extra_wal_paths` (Binance). /api/trades
    # merges all of them. WS timeline replay still reads `wal_path` only —
    # live events arrive via the shared timeline_broker regardless of WAL.
    extra_wal_paths: list[Path] = field(default_factory=list)


def _pnl_view(state: "DashboardState") -> dict:
    """Resolve the dashboard PnL snapshot.

    Prefers the live `PnLAggregator` (#194). Falls back to the static
    `pnl_realtime / pnl_daily / pnl_monthly` fields when no aggregator is
    wired (legacy callers, dashboard-only mode).
    """
    agg = state.pnl_aggregator
    if agg is not None:
        return {
            "realtime": float(agg.realtime),
            "daily": float(agg.daily),
            "monthly": float(agg.monthly),
            "by_strategy": {k: float(v) for k, v in agg.by_strategy.items()},
        }
    return {
        "realtime": state.pnl_realtime,
        "daily": state.pnl_daily,
        "monthly": state.pnl_monthly,
        "by_strategy": {},
    }


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


def _render_dashboard(state: DashboardState, catalog_items: list[dict] | None = None) -> str:
    # 전략 카탈로그 카드 (#178+#180 인라인)
    catalog_cards_html = "".join(_strategy_card(it) for it in (catalog_items or []))

    # Q1: 손익
    pnl = _pnl_view(state)
    pnl_realtime_fmt = f"{pnl['realtime']:,.2f}"
    pnl_daily_fmt = f"{pnl['daily']:,.2f}"
    pnl_monthly_fmt = f"{pnl['monthly']:,.2f}"

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
.run-status{{font-size:1rem;font-weight:700;margin-bottom:8px;color:#bbb}}
.acct-table td{{padding:4px 8px;font-size:.78rem}}
.acct-table td:first-child{{color:#888;width:90px}}
.acct-table td:last-child{{color:#e0e0e0;font-family:'Consolas','Menlo',monospace}}
.nav{{margin-bottom:10px}}
.nav-link{{display:inline-block;background:#1a3a5c;color:#7ecef4;padding:6px 14px;border-radius:5px;text-decoration:none;font-size:.85rem;font-weight:600}}
.nav-link:hover{{background:#2a4a6c;color:#fff}}
.catalog-section{{margin-top:14px}}
.catalog-section > h2{{font-size:.85rem;color:#aaa;text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px}}
.trades-table{{width:100%;border-collapse:collapse;font-size:.78rem}}
.trades-table th,.trades-table td{{padding:6px 8px;text-align:left;border-bottom:1px solid #2a2d3a;font-family:'Consolas','Menlo',monospace}}
.trades-table th{{color:#888;font-weight:600;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;font-family:'Segoe UI',sans-serif}}
.side-buy{{color:#2ecc71;font-weight:700}}
.side-sell{{color:#e74c3c;font-weight:700}}
.state-filled{{color:#2ecc71}}
.state-pending{{color:#f39c12}}
{_STRATEGY_CARD_CSS}
</style>
</head>
<body>
<h1>QTA 로컬 대시보드 — {datetime.now(_KST).strftime('%Y-%m-%d %H:%M:%S KST')}</h1>
<div class="nav">
  <a href="/strategies" class="nav-link">📋 전략 카탈로그 →</a>
  <a href="/shadow_runs" class="nav-link">📊 Shadow Runs (#198) →</a>
</div>
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

  <!-- Q5: 거래 시작/정지 (#182 단계 2) -->
  <div class="card" id="run-control-card">
    <h2>거래 시작/정지</h2>
    <div id="run-status" class="run-status">상태 조회 중…</div>
    <div class="ks-controls">
      <button id="btn-run-start" class="btn btn-reset" onclick="runStart()">거래 시작</button>
      <button id="btn-run-stop" class="btn btn-trigger" onclick="runStop()">거래 정지</button>
    </div>
    <div class="last-ts" id="run-detail">production.yaml 의 등록 전략으로 시작합니다.</div>
  </div>

  <!-- Q6: KIS 계좌 (#182) -->
  <div class="card" id="account-card-kis">
    <h2>KIS 계좌 (paper, KRX)</h2>
    <div id="kis-status" class="run-status">조회 중…</div>
    <table class="acct-table">
      <tbody>
        <tr><td>계좌</td><td id="kis-cano">-</td></tr>
        <tr><td>현금 (KRW)</td><td id="kis-cash">-</td></tr>
        <tr><td>평가금액</td><td id="kis-eval">-</td></tr>
        <tr><td>보유 종목</td><td id="kis-positions">-</td></tr>
      </tbody>
    </table>
    <div class="last-ts" id="kis-detail">.env 의 HANTOO_FAKE_* 인증.</div>
  </div>

  <!-- Q8: 운영 진단 (강제 가시화 — 거래 시작 후 무슨 일이 일어났나) -->
  <div class="card" id="ops-card">
    <h2>운영 진단 (Ops)</h2>
    <div id="ops-summary" class="run-status">조회 중…</div>
    <table class="acct-table">
      <tbody>
        <tr><td>bars 수신</td><td id="ops-bars">-</td></tr>
        <tr><td>strategy_evaluated</td><td id="ops-evals">-</td></tr>
        <tr><td>  └ buy/sell/hold/exc</td><td id="ops-decisions">-</td></tr>
        <tr><td>signal_emitted</td><td id="ops-signals">-</td></tr>
        <tr><td>order_submitted</td><td id="ops-orders">-</td></tr>
        <tr><td>order_filled</td><td id="ops-fills">-</td></tr>
        <tr><td>errors</td><td id="ops-errors">-</td></tr>
        <tr><td>마지막 fill</td><td id="ops-last-fill">-</td></tr>
        <tr><td>마지막 bar 시각</td><td id="ops-last-bar">-</td></tr>
      </tbody>
    </table>
    <div class="last-ts" id="ops-detail">거래 시작 후 카운터가 0 이상이면 정상.</div>
  </div>

  <!-- Q7: Binance Futures 계좌 (#182) -->
  <div class="card" id="account-card-binance">
    <h2>Binance Futures (USDS-M)</h2>
    <div id="bnb-status" class="run-status">조회 중…</div>
    <table class="acct-table">
      <tbody>
        <tr><td>API Key</td><td id="bnb-key">-</td></tr>
        <tr><td>모드</td><td id="bnb-mode">-</td></tr>
        <tr><td>지갑 (USDT)</td><td id="bnb-wallet">-</td></tr>
        <tr><td>가용 (USDT)</td><td id="bnb-avail">-</td></tr>
        <tr><td>미실현손익</td><td id="bnb-upnl">-</td></tr>
        <tr><td>열린 포지션</td><td id="bnb-pos-n">-</td></tr>
      </tbody>
    </table>
    <table class="acct-table" style="margin-top:6px">
      <tbody id="bnb-pos-rows"></tbody>
    </table>
    <div class="last-ts" id="bnb-detail">.env 의 BINANCE_API_KEY/SECRET 인증.</div>
  </div>

</div>

<div class="catalog-section">
  <h2>전략별 포지션 (어떤 전략이 매수/매도했나)</h2>
  <div class="card">
    <table class="trades-table">
      <thead>
        <tr>
          <th>전략</th>
          <th>종목</th>
          <th>매수 (건/수량)</th>
          <th>매도 (건/수량)</th>
          <th>순포지션</th>
          <th>평단가</th>
          <th>실현손익</th>
          <th>최근</th>
        </tr>
      </thead>
      <tbody id="stratpos-tbody">
        <tr><td colspan="8" style="text-align:center;color:#888;padding:18px">조회 중…</td></tr>
      </tbody>
    </table>
  </div>
</div>

<div class="catalog-section">
  <h2>매수/매도 이력 (최근 50건)</h2>
  <div class="card">
    <table class="trades-table">
      <thead>
        <tr>
          <th>시각</th>
          <th>전략</th>
          <th>종목</th>
          <th>방향</th>
          <th>수량</th>
          <th>가격</th>
          <th>상태</th>
          <th>브로커</th>
        </tr>
      </thead>
      <tbody id="trades-tbody">
        <tr><td colspan="8" style="text-align:center;color:#888;padding:18px">조회 중…</td></tr>
      </tbody>
    </table>
  </div>
</div>

<div class="catalog-section">
  <h2>전략 카탈로그</h2>
  <div class="strat-grid">{catalog_cards_html}</div>
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

// 거래 시작/정지 컨트롤 (#182 단계 2)
async function runStart(){{
  document.getElementById('run-status').textContent = '시작 중…';
  const r = await fetch('/api/run/start', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{}})}});
  const d = await r.json();
  runRefresh();
  if(!d.ok) alert('시작 실패: '+(d.reason || JSON.stringify(d)));
}}
async function runStop(){{
  document.getElementById('run-status').textContent = '정지 중…';
  const r = await fetch('/api/run/stop', {{method:'POST'}});
  await r.json();
  runRefresh();
}}
async function runRefresh(){{
  try {{
    const r = await fetch('/api/run/status');
    const d = await r.json();
    const el = document.getElementById('run-status');
    const det = document.getElementById('run-detail');
    if(!d.available){{
      el.textContent = '컨트롤러 미주입 (cmd 모드)';
      det.textContent = '거래 시작 시 cmd 에서 실행하세요: qta.exe --symbols 005930 --broker kis-paper-shadow';
      return;
    }}
    const status = d.status || '?';
    el.textContent = '상태: ' + status;
    el.style.color = status === 'running' ? '#2ecc71' : status === 'error' ? '#e74c3c' : '#bbb';
    if(d.last_error) det.textContent = 'Error: ' + d.last_error;
    else if(d.started_at) det.textContent = '시작: ' + d.started_at + (d.stopped_at ? ' · 종료: ' + d.stopped_at : '');
  }} catch(err) {{ console.warn('run-status', err); }}
}}
runRefresh();
setInterval(runRefresh, 3000);

// 내 계좌 (#182) — KIS + Binance 동시 폴링
function fmtNum(n) {{ return (n||0).toLocaleString('ko-KR'); }}
function setOk(elId, ok, txtOk, txtBad) {{
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent = ok ? txtOk : txtBad;
  el.style.color = ok ? '#2ecc71' : '#e74c3c';
}}
async function acctRefresh() {{
  try {{
    const r = await fetch('/api/account/info');
    const d = await r.json();
    if (!d.available) return;
    // KIS
    const k = d.kis || {{}};
    setOk('kis-status', !!k.ok, '✓ 연결됨 (paper)', '✗ ' + (k.error || '실패'));
    document.getElementById('kis-cano').textContent = k.cano_masked || '-';
    document.getElementById('kis-cash').textContent = k.ok ? fmtNum(k.cash_balance) + ' 원' : '-';
    document.getElementById('kis-eval').textContent = k.ok ? fmtNum(k.eval_amount) + ' 원' : '-';
    document.getElementById('kis-positions').textContent = k.ok ? (k.n_positions || 0) + ' 종목' : '-';
    // Binance
    const b = d.binance || {{}};
    setOk('bnb-status', !!b.ok, '✓ 연결됨', '✗ ' + (b.error || '실패'));
    document.getElementById('bnb-key').textContent = b.api_key_masked || '-';
    document.getElementById('bnb-mode').textContent = b.ok ? (b.testnet ? 'testnet' : 'live') + ' · ' + (b.base_url_short||'') : '-';
    document.getElementById('bnb-wallet').textContent = b.ok ? fmtNum(b.wallet_balance_usdt) + ' USDT' : '-';
    document.getElementById('bnb-avail').textContent = b.ok ? fmtNum(b.available_usdt) + ' USDT' : '-';
    // #238 — 실제 broker 포지션 + 미실현손익
    const upnlEl = document.getElementById('bnb-upnl');
    if (upnlEl) {{
      const u = b.total_unrealized_pnl;
      upnlEl.textContent = (b.ok && u != null) ? (u >= 0 ? '+' : '') + fmtNum(u) + ' USDT' : '-';
      upnlEl.style.color = (u == null) ? '' : (u >= 0 ? '#2ecc71' : '#e74c3c');
    }}
    const posNEl = document.getElementById('bnb-pos-n');
    if (posNEl) posNEl.textContent = b.ok ? (b.n_positions || 0) + ' 개' : '-';
    const posRows = document.getElementById('bnb-pos-rows');
    if (posRows) {{
      const ps = (b.ok && b.positions) ? b.positions : [];
      posRows.innerHTML = ps.map(p => {{
        const sideCls = p.side === 'LONG' ? 'side-buy' : 'side-sell';
        const pnlCls = p.unrealized_pnl >= 0 ? 'side-buy' : 'side-sell';
        return `<tr><td class="${{sideCls}}">${{escHtml(p.symbol)}} ${{escHtml(p.side)}}</td>`
          + `<td>${{escHtml(p.amt)}} @ ${{fmtNum(p.entry_price)}} `
          + `<span class="${{pnlCls}}">(${{p.unrealized_pnl>=0?'+':''}}${{fmtNum(p.unrealized_pnl)}})</span></td></tr>`;
      }}).join('');
    }}
  }} catch (err) {{ console.warn('account', err); }}
}}
// 30s 간격 + in-flight 가드 — KIS 모의 초당 한도 보호 (5s 폴링이 retry chain 과
// 누적해 EGW00201 폭주시키던 #238 hotfix).
let _acctInflight = false;
async function acctRefreshGuarded() {{
  if (_acctInflight) return;
  _acctInflight = true;
  try {{ await acctRefresh(); }}
  finally {{ _acctInflight = false; }}
}}
acctRefreshGuarded();
setInterval(acctRefreshGuarded, 30000);

// 운영 진단 카드 — bars/evals/orders/fills 카운터 폴링
async function opsRefresh() {{
  try {{
    const r = await fetch('/api/ops');
    const d = await r.json();
    if (!d.available) return;
    const set = (id, v) => {{ const el = document.getElementById(id); if (el) el.textContent = v; }};
    set('ops-bars',    fmtNum(d.bars_seen));
    set('ops-evals',   fmtNum(d.strategy_evaluated));
    const dec = d.decisions || {{}};
    set('ops-decisions', `${{fmtNum(dec.buy||0)}} / ${{fmtNum(dec.sell||0)}} / ${{fmtNum(dec.hold||0)}} / ${{fmtNum(dec.exception||0)}}`);
    set('ops-signals', fmtNum(d.signal_emitted));
    set('ops-orders',  fmtNum(d.order_submitted));
    set('ops-fills',   fmtNum(d.order_filled));
    set('ops-errors',  fmtNum(d.errors));
    set('ops-last-fill', d.last_fill_detail || '-');
    set('ops-last-bar',  d.last_bar_ts ? d.last_bar_ts.slice(0,19).replace('T',' ') : '-');
    const summaryEl = document.getElementById('ops-summary');
    if (summaryEl) {{
      const trading = (d.order_filled||0) > 0 || (d.order_submitted||0) > 0;
      summaryEl.textContent = trading ? '✓ 거래 발생 중' : ((d.strategy_evaluated||0) > 0 ? '⏳ 시그널 대기' : '대기 중 (시세 미수신)');
      summaryEl.style.color = trading ? '#2ecc71' : '#bbb';
    }}
  }} catch (err) {{ console.warn('ops', err); }}
}}
opsRefresh();
setInterval(opsRefresh, 3000);

// 거래 이력 — WAL order_filled/order_submitted 폴링 (5s)
function escHtml(s) {{
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
}}
async function tradesRefresh() {{
  try {{
    const r = await fetch('/api/trades?limit=50');
    const d = await r.json();
    const tb = document.getElementById('trades-tbody');
    if (!tb) return;
    const trades = d.trades || [];
    if (trades.length === 0) {{
      tb.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#888;padding:18px">거래 이력 없음 (거래 시작 후 첫 체결을 기다리는 중)</td></tr>';
      return;
    }}
    tb.innerHTML = trades.map(t => {{
      const sideCls = t.side === 'buy' ? 'side-buy' : t.side === 'sell' ? 'side-sell' : '';
      const stateCls = t.filled ? 'state-filled' : 'state-pending';
      const stateTxt = t.filled ? '체결' : '제출';
      const ts = (t.ts || '').slice(0,19).replace('T',' ');
      return `<tr>
        <td>${{escHtml(ts)}}</td>
        <td>${{escHtml(t.strategy_id)}}</td>
        <td>${{escHtml(t.symbol)}}</td>
        <td class="${{sideCls}}">${{escHtml((t.side || '').toUpperCase())}}</td>
        <td>${{escHtml(t.qty)}}</td>
        <td>${{escHtml(t.price)}}</td>
        <td class="${{stateCls}}">${{stateTxt}}</td>
        <td>${{escHtml(t.broker)}}</td>
      </tr>`;
    }}).join('');
  }} catch (err) {{ console.warn('trades', err); }}
}}
tradesRefresh();
setInterval(tradesRefresh, 5000);

// 전략별 포지션 — WAL strategy_id 집계 (5s)
async function stratPosRefresh() {{
  try {{
    const r = await fetch('/api/strategy_positions');
    const d = await r.json();
    const tb = document.getElementById('stratpos-tbody');
    if (!tb) return;
    const rows = d.strategies || [];
    if (rows.length === 0) {{
      tb.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#888;padding:18px">아직 거래한 전략 없음</td></tr>';
      return;
    }}
    tb.innerHTML = rows.map(s => {{
      const net = s.net_qty || 0;
      const netCls = net > 0 ? 'side-buy' : net < 0 ? 'side-sell' : '';
      const pnl = s.realized_pnl;
      const pnlCls = pnl == null ? '' : (pnl >= 0 ? 'side-buy' : 'side-sell');
      const pnlTxt = pnl == null ? '-' : (pnl >= 0 ? '+' : '') + Number(pnl).toLocaleString('ko-KR');
      const avg = s.avg_price == null ? '-' : Number(s.avg_price).toLocaleString('ko-KR');
      const ts = (s.last_ts || '').slice(0,19).replace('T',' ');
      return `<tr>
        <td>${{escHtml(s.strategy_id)}}</td>
        <td>${{escHtml(s.symbol)}}</td>
        <td class="side-buy">${{s.buy_n}} / ${{escHtml(s.buy_qty)}}</td>
        <td class="side-sell">${{s.sell_n}} / ${{escHtml(s.sell_qty)}}</td>
        <td class="${{netCls}}">${{escHtml(net)}}</td>
        <td>${{avg}}</td>
        <td class="${{pnlCls}}">${{pnlTxt}}</td>
        <td>${{escHtml(ts)}}</td>
      </tr>`;
    }}).join('');
  }} catch (err) {{ console.warn('stratpos', err); }}
}}
stratPosRefresh();
setInterval(stratPosRefresh, 5000);

{_STRATEGY_TOGGLE_JS}
</script>
</body>
</html>"""


# ---- Shared CSS/JS used by both / (main dashboard) and /strategies ---------

_STRATEGY_CARD_CSS = """
.strat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.strat-card{background:#1a1d27;border:1px solid #2a2d3a;border-radius:8px;padding:14px;display:flex;flex-direction:column;gap:10px}
.strat-card-disabled{opacity:.55;border-color:#5a2a2a}
.strat-head{display:flex;justify-content:space-between;align-items:baseline}
.strat-name{color:#fff;font-weight:700;font-size:1rem;text-decoration:none}
.strat-name:hover{color:#7ecef4}
.strat-status{font-size:.7rem;background:#2a2d3a;border-radius:3px;padding:2px 6px;color:#bbb;text-transform:uppercase}
.strat-prod{font-size:.65rem;border-radius:3px;padding:2px 5px;font-weight:700;letter-spacing:.04em}
.prod-active{background:#1a3a1a;color:#7ef47e;border:1px solid #2a5a2a}
.prod-commented{background:#3a2a1a;color:#f4b07e;border:1px solid #5a3a1a}
.prod-absent{background:#2a2a2a;color:#888;border:1px dashed #444}
.strat-meta{display:flex;gap:12px;flex-wrap:wrap;font-size:.75rem;color:#aaa}
.strat-summary{font-size:.78rem;color:#cfd5e0;line-height:1.45;background:#0f1117;border-left:3px solid #7ecef4;padding:8px 10px;border-radius:4px;white-space:pre-line}
.strat-metrics{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.strat-metrics > div{background:#0f1117;border-radius:5px;padding:6px 8px;display:flex;justify-content:space-between;font-size:.78rem}
.m-label{color:#888}
.m-val{color:#ddd;font-weight:600}
.strat-toggle-row{display:flex;justify-content:space-between;align-items:center;border-top:1px solid #2a2d3a;padding-top:10px}
.strat-state{font-size:.78rem;font-weight:700}
.strat-on{color:#2ecc71}
.strat-off{color:#e74c3c}
.switch{position:relative;display:inline-block;width:46px;height:24px}
.switch input{opacity:0;width:0;height:0}
.slider{position:absolute;cursor:pointer;inset:0;background:#3a3f4d;border-radius:24px;transition:.2s}
.slider:before{content:"";position:absolute;height:18px;width:18px;left:3px;bottom:3px;background:#e0e0e0;border-radius:50%;transition:.2s}
input:checked + .slider{background:#2ecc71}
input:checked + .slider:before{transform:translateX(22px)}
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


def _strategy_card(item: dict) -> str:
    sid = html.escape(str(item.get("id", "")))
    name = html.escape(str(item.get("name", sid)))
    status = html.escape(str(item.get("status", "")))
    instruments = html.escape(", ".join(item.get("instruments") or []))
    timeframe = html.escape(str(item.get("timeframe", "")))
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
    return f"""
    <div class="{card_cls}" data-strategy-id="{sid}">
      <div class="strat-head">
        <a class="strat-name" href="/strategies/{sid}">{name}</a>
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
      <div class="strat-toggle-row">
        <span class="strat-state {state_cls}">{state_label}</span>
        <label class="switch">
          <input type="checkbox" class="strat-toggle" data-strategy-id="{sid}"{checked_attr}>
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

    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        return HTMLResponse(content=_render_dashboard(state, _enriched_catalog()))

    @app.get("/metrics")
    async def metrics() -> Response:
        data = generate_latest(state.metrics.registry)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/pnl")
    async def api_pnl() -> JSONResponse:
        return JSONResponse(_pnl_view(state))

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
        """
        paths: list[Path] = []
        if state.wal_path is not None and Path(state.wal_path).exists():
            paths.append(Path(state.wal_path))
        for p in state.extra_wal_paths or []:
            if p is not None and Path(p).exists() and Path(p) not in paths:
                paths.append(Path(p))
        agg: dict[str, dict] = {}
        for p in paths:
            events, _ = wal_replay(p)
            for ev in events:
                if ev.event_type not in ("order_acked", "order_filled", "fill_received"):
                    continue
                pl = ev.payload or {}
                sid = pl.get("strategy_id", "") or "?"
                side = (pl.get("side") or "").lower()
                try:
                    qty = float(pl.get("qty") or pl.get("quantity") or 0)
                except (TypeError, ValueError):
                    qty = 0.0
                try:
                    px = float(pl.get("price") or pl.get("fill_price") or 0)
                except (TypeError, ValueError):
                    px = 0.0
                a = agg.setdefault(sid, {
                    "strategy_id": sid, "symbol": pl.get("symbol", ""),
                    "buy_n": 0, "buy_qty": 0.0, "sell_n": 0, "sell_qty": 0.0,
                    "_px_sum": 0.0, "_px_n": 0, "last_ts": "",
                })
                if not a["symbol"]:
                    a["symbol"] = pl.get("symbol", "")
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
        out = []
        for sid, a in sorted(agg.items()):
            avg_px = (a["_px_sum"] / a["_px_n"]) if a["_px_n"] > 0 else None
            out.append({
                "strategy_id": sid,
                "symbol": a["symbol"],
                "buy_n": a["buy_n"],
                "buy_qty": round(a["buy_qty"], 6),
                "sell_n": a["sell_n"],
                "sell_qty": round(a["sell_qty"], 6),
                "net_qty": round(a["buy_qty"] - a["sell_qty"], 6),
                "avg_price": round(avg_px, 4) if avg_px is not None else None,
                "realized_pnl": pnl_by.get(sid),
                "last_ts": a["last_ts"],
            })
        return JSONResponse({"available": True, "strategies": out})

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
        return Path(__file__).resolve().parents[2] / "configs" / "orchestrator" / "production.yaml"

    def _enriched_catalog() -> list[dict]:
        items = load_strategy_catalog(_resolve_specs_dir())
        prod_status = load_production_status(_resolve_production_yaml())
        orch = state.orchestrator
        agg = state.pnl_aggregator
        for it in items:
            if orch is not None and hasattr(orch, "is_enabled"):
                it["enabled"] = bool(orch.is_enabled(it["id"]))
            else:
                it["enabled"] = True
            it["pnl_today"] = float(agg.daily_for(it["id"])) if agg is not None else 0.0
            # production.yaml registration visibility (18 specs vs 11 active).
            it["production_status"] = prod_status.get(it["id"], "absent")
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

    return app


# Standalone entry point: uvicorn src.dashboard.app:app
app = create_app()

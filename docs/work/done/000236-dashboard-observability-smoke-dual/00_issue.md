---
type: work-done
id: 00_issue
name: "#236 — 대시보드 실거래 가시화 + smoke-dual 통로 검증"
status: done
issue: 236
branch: feat/000236-dashboard-observability-smoke-dual
owner: 성시우
started: 2026-05-15
finished: 2026-05-15
---

# #236 — 대시보드 실거래 가시화 + smoke-dual 통로 검증

## 배경

`qta.exe` (dashboard-only 모드) 에서 "거래 시작" 버튼을 눌러도 거래가 실제로
발생하는지 대시보드에서 확인할 수 없는 문제. 사용자가 `daily_check_kis.ps1`
PowerShell 진단으로만 dispatch/fill 상태를 확인할 수 있었음.

진단 결과 (3가지 동시 원인):

1. **대시보드-only observability wiring 누락** — `scripts/live_run.py` `_serve()`
   가 `pnl_aggregator` / `position_store` / `wal_path` 를 DashboardState 에
   주입하지 않아 거래가 발생해도 PnL=0, 타임라인 빈 상태로 보였음.
2. **활성 11개 전략의 신호 빈도 부족** — momo-kis-v1 (15m RSI divergence) +
   universe-scan 6종 (5일 리밸). live-scanner 5종은 `production.yaml` 에 commented
   (5y bench 미통과). 가시 검증을 위한 신호 보장 안 됨.
3. **dispatch 카운터가 대시보드 UI 미노출** — PR #235 에서 emit 시작된
   `strategy_evaluated` WAL event 가 PowerShell 진단에만 보임.

추가로 대시보드 헤더 UTC 표기, 전략 카탈로그가 18개 spec 전부 노출 (실제
production.yaml 활성 11개) 등 가시성 격차 동반.

## 완료 기준 (AC)

- [x] 대시보드 헤더 KST 표기 (Asia/Seoul)
- [x] 대시보드 "거래 시작" 시 PnL/타임라인/포지션이 실시간 갱신 (dashboard-only
      모드에서도 CLI 모드와 동일 wiring — `_serve()` + `_run_pipeline_attached`)
- [x] 전략 카탈로그 각 카드에 production.yaml registered 여부 (active/commented/absent) 뱃지
- [x] 운영 진단 패널 — bars 수신·strategy_evaluated 카운터 + decision breakdown
      + 마지막 signal/order/fill 시각 + 마지막 fill 상세 (`OpsCounters`)
- [x] 매수/매도 이력 패널 — WAL `order_filled` + `order_submitted` 최신 50건,
      side 컬러링, KIS + Binance WAL 자동 머지
- [x] smoke 전략 (`smoke-1m-roundtrip`) — 매 1분 buy/sell 토글, `SMOKE_TEST_ENABLED=1`
      env gate (없으면 hold)
- [x] smoke-dual broker mode — 단일 "거래 시작" 클릭으로 KIS paper 005930 +
      Binance testnet BTCUSDT 병렬 실행, observability 공유, 별도 WAL
- [x] 단위 테스트 통과 (smoke 5건 + 회귀 92건)
- [x] check_strategy_completeness 0 error (20 전략 · warn 39 pre-existing)

## 작업 내역

### 신규 파일

- `src/dashboard/ops_counters.py` — `OpsCounters` 클래스: WAL 이벤트 흡수해
  bars/evals/orders/fills 카운터 + decision breakdown + 마지막 ts/detail 유지.
  `_wal_observer` fan-out 에 endpoint 하나 추가하는 한 줄 wire.
- `src/backtest/strategies/smoke_1m_roundtrip.py` — `Smoke1mRoundtrip`. 매 bar
  buy/sell 토글, `SMOKE_TEST_ENABLED=1` env 없으면 hold. 심볼별 독립 state.
  `SMOKE_SIZE_FRACTION` env 와 constructor 의 `size_fraction` 으로 사이즈 조절.
- `docs/specs/strategies/smoke-1m-roundtrip-kis.md` + `-binance.md` — 두 entry
  분리 spec (completeness checker 가 id 별 spec 매칭 요구).
- `tests/backtest/test_smoke_1m_roundtrip.py` — 5 case: disabled→hold,
  enabled→buy/sell 교차, 심볼별 독립, env size override, constructor override.

### 수정

- `src/dashboard/app.py`
  - `_KST = ZoneInfo("Asia/Seoul")` 추가 + 헤더 KST 표기.
  - `DashboardState` 에 `ops_counters: OpsCounters | None` + `extra_wal_paths: list[Path]` 추가.
  - `_strategy_card` 에 `production_status` (active/commented/absent) 뱃지 + CSS.
  - 새 endpoint `/api/ops`, `/api/trades` (멀티 WAL 머지 + newest-first sort).
  - Q8 "운영 진단" 카드 + "매수/매도 이력" 테이블 카드 + 폴링 JS (3s/5s).
- `src/dashboard/strategy_catalog.py` — `load_production_status` 추가.
  active / commented (regex `# - id:`) / absent 3분류.
- `scripts/live_run.py`
  - `_serve()` 가 `PnLAggregator` + `StrategyPositionStore` + `OpsCounters` 를
    선제 생성해 DashboardState 에 주입 — dashboard-only 모드에서도 거래 시작
    버튼이 CLI 모드와 동일한 observability 확보.
  - `_run_pipeline_attached` + `_build_pipeline_factory` keyword-only 인자 확장,
    `_wal_observer` 가 ops_counters 까지 fan-out.
  - `_run_pipeline` (CLI path) 도 동일하게 ops_counters wire.
  - 신규 `_run_smoke_dual` — KIS branch + Binance branch 를 `asyncio.gather` 로
    병렬 실행. 별도 WAL 파일 (`logs/live/<run_id>-kis/`, `<run_id>-binance/`),
    공유 wal_observer 로 timeline/pnl/position/ops fan-out.
  - `--broker` choices 에 `smoke-dual` 추가. SMOKE_TEST_ENABLED=1 env 가 set 일
    때 거래 시작 버튼이 자동으로 smoke-dual 선택.
- `configs/orchestrator/production.yaml` — smoke entries 2개 추가 (kis + binance).
  env gate 가 strategy 코드 안에 있어 등록만 되고 거래 zero.
- `src/backtest/strategies/.ai.md` — smoke 전략 등재 + 운영 사용 금지 명시.

### 검증

- `pytest tests/backtest/test_smoke_1m_roundtrip.py tests/test_run_controller.py
  tests/test_strategy_catalog_loader.py tests/test_strategy_catalog_integration.py
  tests/test_dashboard.py tests/test_dashboard_strategies.py
  tests/test_dashboard_ws_timeline.py tests/test_dashboard_pnl_integration.py
  tests/test_live_run_dashboard_wiring.py` → 92 passed.
- `python scripts/check_strategy_completeness.py` → 20 전략 · 0 error · 39 pre-existing warn.
- `python scripts/check_invariants.py` → 신규 위반 없음 (5 draft 경고는 다른 작업의 잔여).

## Out of Scope (별도 이슈 후보)

- WS replay (`/ws/timeline`) 멀티 WAL timestamp 머지 — 현재 `state.wal_path`
  (KIS primary) 만 replay. Binance 라이브 이벤트는 broker 통해 발행되지만
  과거 이벤트는 페이지 새로고침 시 안 보임.
- smoke-dual KIS branch 의 장외 시간 REST 오류 graceful — 현재 errors 카운터에
  KIS rate-limit/조회 실패가 누적. 거동에 영향 없지만 카운터 노이즈.
- smoke 전략 활성 시간 제한 (1시간 후 자동 OFF) — 현재 사용자가 직접 env 제거 필요.

## 운영 절차

```powershell
# 1. .env 보강
SMOKE_TEST_ENABLED=1
# (HANTOO_FAKE_*, BINANCE_DEMO_API_KEY/SECRET 이미 있어야 함)

# 2. qta.exe 실행 (no-args → dashboard-only)
.\qta.exe

# 3. 대시보드에서 "거래 시작" → smoke-dual 자동 선택
#    - 운영 진단 카드: bars/evals/orders/fills 카운터 증가
#    - 거래 이력 카드: 매 1분 BUY/SELL 추가
#    - KIS branch: KRX 09:00-15:30 KST 만 동작 (오늘 2026-05-15 금)
#    - Binance branch: 24/7 동작

# 4. 검증 끝나면 .env 의 SMOKE_TEST_ENABLED 제거 (운영 비용 누적 방지)
```

---
type: work-done
id: 01_plan
name: "Issue #198 — 구현 계획"
status: active
---

# 구현 계획 — #198 대시보드 Shadow Runs 뷰어

## 완료 기준 (00_issue.md 참조)
- [ ] `GET /shadow_runs` (HTML 페이지)
- [ ] `GET /api/shadow_runs` (목록 JSON)
- [ ] `GET /api/shadow_runs/{run_id}` (상세 JSON)
- [ ] 거래소·종목·봉·alive 자동 분류
- [ ] 단위 테스트 + e2e 테스트

## 아키텍처 — 기존 dashboard 패턴 그대로 따름

`/strategies` 와 `_enriched_catalog()` 가 좋은 참고 모델. 같은 구조 + 다른 데이터 소스.

```
src/dashboard/
├── app.py                      ← 라우트 3개 추가
├── strategy_catalog.py         ← 참고 (production.yaml 로딩 패턴)
└── shadow_runs.py              ← 신규 (logs/shadow/* WAL 로딩)

tests/
└── test_dashboard_shadow_runs.py  ← 신규
```

## 구현 단계

### Phase 1: `src/dashboard/shadow_runs.py` 신규 (45분)

**책임**: `logs/shadow/*` 디렉토리 스캔 → 각 run 의 요약 통계 dict 반환.

```python
# 핵심 함수
def discover_shadow_runs(log_dir: Path) -> list[dict]:
    """logs/shadow/* 디렉토리 목록 + 각 WAL 의 요약 통계 반환."""

def load_run_detail(log_dir: Path, run_id: str) -> dict:
    """특정 run 의 상세: PnL / 거래수 / 현재 포지션 / Sharpe (optional)."""

# 분류 헬퍼
def classify_exchange(symbol: str) -> Literal["binance", "kis", "unknown"]:
    """BTCUSDT/ETHUSDT 등 → binance, 6자리 숫자 → kis"""

def classify_timeframe(run_id: str) -> Literal["1h", "4h", "EOD", "unknown"]:
    """r4-switch → 4h, r6-switch → 1h, momo-kis-v1 → EOD"""

def classify_alive(last_ts: datetime, timeframe: str) -> Literal["alive", "idle", "dead"]:
    """봉 단위 × 2 보다 오래되면 dead, 봉 단위보다 최근이면 alive"""
```

**graceful empty WAL**: 디렉토리는 있지만 wal.jsonl 없거나 비었으면 → `events=0, last_ts=None, status="idle"` 반환. 에러 X.

### Phase 2: `src/dashboard/app.py` 라우트 3개 추가 (30분)

```python
# state 에 shadow_log_dir 추가 (기본 = logs/shadow)
state.shadow_log_dir: Path = Path("logs/shadow")

@app.get("/api/shadow_runs")
async def api_shadow_runs() -> JSONResponse:
    runs = discover_shadow_runs(state.shadow_log_dir)
    return JSONResponse(runs)

@app.get("/api/shadow_runs/{run_id}")
async def api_shadow_run_detail(run_id: str) -> JSONResponse:
    detail = load_run_detail(state.shadow_log_dir, run_id)
    if detail is None:
        raise HTTPException(404)
    return JSONResponse(detail)

@app.get("/shadow_runs", response_class=HTMLResponse)
async def shadow_runs_page() -> HTMLResponse:
    runs = discover_shadow_runs(state.shadow_log_dir)
    return HTMLResponse(_render_shadow_runs(runs))
```

### Phase 3: HTML 페이지 (30분)

`/strategies` 페이지 패턴 그대로 따름 (카드 그리드). 카드 1개당 1 run.

```
┌──────────────────────────────────────┐
│ 🟢 phase1-r4-switch-BTCUSDT          │  ← 디렉토리명 + alive 색
│ Binance · BTCUSDT · 4h · r4-switch   │  ← 분류 결과
│ ─────────────────────────────────    │
│ 마지막 활동: 23분 전                  │
│ 거래수: entry 0 / exit 0             │  ← 빈 WAL 케이스
│ 누적 PnL: 0 USDT                     │
│ 현재 포지션: (없음)                   │
└──────────────────────────────────────┘
```

색상 룰:
- 🟢 alive = 마지막 활동이 봉 단위 × 1.5 이내
- 🟡 idle = 1.5 ~ 2배 (정상 가능)
- 🔴 dead = 2배 초과 (데몬 죽었거나 신호 안 떨어짐 — 사용자 확인 필요)

메인 대시보드 `/` 에 `<a href="/shadow_runs">📊 Shadow Runs (#198)</a>` nav link 추가.

### Phase 4: 테스트 (30분)

`tests/test_dashboard_shadow_runs.py`:

1. `test_discover_empty_log_dir` — 빈 디렉토리 → `[]`
2. `test_discover_dir_no_wal` — 디렉토리 있지만 wal.jsonl 없음 → `[{events:0, status:"idle"}]`
3. `test_discover_dir_with_events` — 가짜 wal.jsonl (3 events) → 통계 정확
4. `test_classify_exchange_btcusdt` — BTCUSDT → "binance"
5. `test_classify_exchange_kis_005930` — 005930 → "kis"
6. `test_classify_timeframe_r4_r6` — r4-switch → 4h, r6-switch → 1h
7. `test_classify_alive_thresholds` — 봉별 임계
8. `test_load_run_detail_with_position` — broker.from_wal 로 포지션 복원
9. **e2e: 실제 logs/shadow/phase1-r4-switch-BTCUSDT 빈 WAL 처리** — graceful

### Phase 5: 통합 검증 + .ai.md (15분)

- `python -m pytest tests/test_dashboard_shadow_runs.py -v` → 그린
- 풀 회귀
- check_invariants
- `src/dashboard/.ai.md` 갱신 — shadow_runs 섹션 추가

## 의존성
- 기존 dashboard 코드 (#178/#180/#194 등) — 라우트 패턴 재활용
- `src/live/wal.py::replay` — WAL 파싱
- `src/execution/paper_broker.py::PaperBroker.from_wal` — broker 상태 복원

## 충돌 위험
- 메인 dashboard `/` 페이지에 nav link 1줄 추가 → 다른 PR 과 충돌 가능
- DashboardState 에 `shadow_log_dir` 필드 1줄 추가 → 다른 PR 과 충돌 가능

→ **본 PR 변경 최소화** — `/shadow_runs` 페이지 자체는 독립 모듈, app.py 변경은 라우트 3개 + state 1필드 + nav link 1줄만.

## 변경 파일

| 파일 | 변경 |
|---|---|
| `src/dashboard/shadow_runs.py` (신규) | discover + classify + load_detail |
| `src/dashboard/app.py` | 라우트 3개 + state.shadow_log_dir + nav link |
| `tests/test_dashboard_shadow_runs.py` (신규) | 9 테스트 케이스 |
| `src/dashboard/.ai.md` | shadow_runs 섹션 |

## 위험 / 롤백
- shadow_runs 모듈 import 실패 시 → app.py 정상 시작 보장 (`try/except` 로 graceful)
- 신규 라우트만 추가 → 기존 라우트 영향 0
- 테스트는 가짜 디렉토리 + 가짜 WAL 로 → CI 안정

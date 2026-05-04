# 01 Plan — #181 매매 타임라인 실시간 스트리밍 (WebSocket)

## 선행 의존성 (실측 확인됨, 2026-05-04)
- PR #169 — MERGED 2026-05-03 (FastAPI 로컬 대시보드)
- 이슈 #125 — CLOSED (`src/dashboard/app.py`, `DashboardState.timeline_events` 보유)
- 이슈 #80 — CLOSED (`src/live/wal.py`, `WALEvent`, `replay()` 보유)

## AC 체크리스트
- [x] `/ws/timeline` WebSocket endpoint — JSON 이벤트 스트림 (signal_emitted, metalabeler_decision, order_placed, fill_received)
- [x] WAL 의 기존 이벤트를 WS 로 fan-out (live + replay) — `WAL.observer` 훅 + `wal_replay()` query
- [x] HTML 타임라인 UI — 최근 100건 무한스크롤 — JS prepend + DOM 100 cap
- [x] 백압 (back-pressure) 처리 — 클라이언트 느릴 때 drop 정책 (drop oldest) — `TimelineBroker._put_drop_oldest`
- [x] 단위 테스트 (FastAPI TestClient WebSocket) — 14건 PASS

## 구현 계획

### 1. `src/dashboard/timeline_broker.py` (신규)
in-process pub/sub. 가입자별 bounded `asyncio.Queue`(maxsize=100), publish 시 가득 차면 oldest drop 후 put.

```python
class TimelineBroker:
    def subscribe(self, maxsize: int = 100) -> asyncio.Queue
    def unsubscribe(self, queue: asyncio.Queue) -> None
    def publish(self, event: dict) -> None  # nowait, drop-oldest on overflow
    @property
    def subscriber_count(self) -> int
    @property
    def dropped_total(self) -> int
```

### 2. 이벤트 스키마 (4종 + canonical 정의)
WS payload 는 그대로 WAL 의 `WALEvent` JSON dict (`ts`, `event_type`, `schema_version`, `payload`).
AC 4종 `event_type` 값:
- `signal_emitted` — 신호 발생 (payload: symbol, direction, score)
- `metalabeler_decision` — 메타라벨러 통과/거부 (payload: client_order_id, decision, confidence)
- `order_placed` — 주문 접수 (payload: client_order_id, side, qty, price)
- `fill_received` — 체결 수신 (payload: client_order_id, fill_price, fill_qty)

`src/dashboard/timeline_events.py` 에 string constant + payload typing 정의.

### 3. `src/dashboard/app.py` 확장
- `DashboardState`: `timeline_broker: TimelineBroker | None`, `wal_path: Path | None` 추가.
- `create_app()`: state.timeline_broker 가 None 이면 새로 생성.
- 신규 `WS /ws/timeline?replay=N` (기본 N=100):
  - 연결 시 `wal_path` 가 있으면 `replay(path)` 호출, 끝부분 N건만 전송 (`{"phase":"replay","event":...}` or 단순 dict).
  - 그 후 broker subscribe → 큐에서 받아 send_json.
  - WebSocketDisconnect 발생 시 unsubscribe.
- HTML Q3 panel 에 WS 클라이언트 JS 삽입:
  - `WebSocket('ws://'+location.host+'/ws/timeline?replay=100')`
  - 도착 이벤트 prepend, DOM 100 행 초과 시 oldest 제거.
  - 연결 끊기면 1초 후 재연결.

### 4. WAL → broker fan-out
- `WAL.__init__` 에 `observer: Callable[[WALEvent], None] | None = None` 추가.
- `WAL.write()` 가 fsync 성공 후 `observer(event)` 호출 (예외 swallow + log warn — WAL 쓰기는 절대 실패하면 안 됨).
- 대시보드 startup 에서 `wal.observer = lambda ev: state.timeline_broker.publish(asdict(ev))` 와이어링.
- 기존 callsites 영향 없음 (observer 디폴트 None).

### 5. 테스트 (`tests/test_dashboard_ws_timeline.py`)
- `test_ws_replay_empty` — WAL 없음 → 즉시 live mode (replay phase 0건).
- `test_ws_replay_last_n` — WAL 에 150건 → replay=100 시 마지막 100건만 수신.
- `test_ws_live_publish` — 연결 후 broker.publish → 클라가 같은 dict 수신.
- `test_ws_drop_oldest` — 큐 overflow 시 oldest drop, newest 유지.
- `test_ws_4_event_types` — signal_emitted/metalabeler_decision/order_placed/fill_received 4종 모두 통과.
- `test_ws_disconnect_unsubscribes` — 클라 disconnect 시 broker.subscriber_count == 0.
- `test_wal_observer_callback` — WAL.write 시 observer 호출 검증.

### 6. `.ai.md` 업데이트
`src/dashboard/.ai.md` 에 `/ws/timeline` endpoint, `timeline_broker.py`, `timeline_events.py` 추가.

## 변경 영향 범위
- `src/dashboard/app.py` — WS endpoint + JS 추가
- `src/dashboard/timeline_broker.py` — 신규
- `src/dashboard/timeline_events.py` — 신규 (event_type 상수)
- `src/live/wal.py` — observer 훅 추가 (backward-compatible)
- `src/dashboard/.ai.md` — 갱신
- `tests/test_dashboard_ws_timeline.py` — 신규

## 리스크
- **WAL observer 예외**: 대시보드 broker 가 죽었을 때 WAL.write 가 영향받지 않게 try/except 필수. → observer 콜백을 try/except 로 감싸고 log.warning.
- **다중 구독 메모리**: 큐당 최대 100건 제한 + 명시적 unsubscribe 로 누수 방지.
- **무한스크롤 vs 클라 DOM cap**: AC 가 "최근 100건" 이라 DOM 도 100 cap 으로 일치시켜 스크롤 = 최근 N 만 노출. 진정한 lazy load 가 아니라 "live 100건 윈도우" 로 정의.
- **TestClient WS 동기 API**: FastAPI TestClient.websocket_connect 는 동기 컨텍스트. broker.publish 는 별도 스레드 또는 startup 후 동기 publish 가능 (publish 는 nowait).

---

## 작업 내역

### 2026-05-04 구현 완료
- 신규: `src/dashboard/timeline_broker.py` (drop-oldest queue), `src/dashboard/timeline_events.py` (4 상수)
- 수정: `src/live/wal.py` (`observer` 훅), `src/dashboard/app.py` (`DashboardState.timeline_broker`/`wal_path`, `WS /ws/timeline`, JS WS 클라이언트)
- 신규 테스트: `tests/test_dashboard_ws_timeline.py` 14건
- 회귀: `test_dashboard.py`(16) + `test_wal.py`(9) + WAL 인티그레이션(8) 모두 GREEN
- 문서: `src/dashboard/.ai.md`, `src/live/.ai.md` 갱신

### EXE 통합은 #177 로 이관 (2026-05-04 합의)
본 PR 은 라이브러리·모듈 단위 완성(AC 5건 충족)에 그치며, 다음 3 항목은 **#177 (configs/orchestrator/production.yaml + EXE 재빌드)** 에서 처리한다:
1. `qta.spec` 의 `hiddenimports` 에 `src.dashboard`, `src.dashboard.app`, `src.dashboard.timeline_broker`, `src.dashboard.timeline_events`, `uvicorn`, `starlette.websockets` 추가.
2. `scripts/live_run.py` 에 `--dashboard-port` 플래그 + dashboard FastAPI 백그라운드 task 기동 옵션 추가.
3. `run_shadow_loop` 부트스트랩에서 `WAL(path, observer=lambda ev: state.timeline_broker.publish(asdict(ev)))` 와이어링.

이유: #177 가 마침 production.yaml 확장 + EXE 재빌드 작업이라, spec/live_run/wiring 변경을 같은 PR 에 묶는 편이 PyInstaller 회귀 테스트 비용을 한 번에 흡수할 수 있다.

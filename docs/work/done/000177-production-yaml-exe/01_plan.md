# [#177] 구현 계획

> 작성: 2026-05-04 (스코프 확장: 단일 PR 단계 9개)

## AC 체크리스트
- [ ] `configs/orchestrator/production.yaml` 작성 — 5전략 등록 + 메타라벨러 운영자 활성화 절차 + 리스크 임계 도큐
- [ ] `qta.spec` 의 datas 에 production.yaml 명시적 포함 검증 (테스트로 boot)
- [ ] EXE 재빌드 + `qta.exe --symbols 005930 --max-iterations 5 --broker paper-only` 5전략 로드 + 시그널 발생 확인
- [ ] 백서 §10-2 "production.yaml 위치" subsection 추가

## 단계별 계획

### 1. production.yaml 5전략 등록 + metalabeler graceful skip
- `configs/orchestrator/production.yaml` — `momo-btc-v2`, `momo-vol-filtered`, `meanrev-pairs`, `breakout-donchian`, `momo-kis-v1` 5전략. `momo-btc-v2-meta` 는 commented 블록 (운영자가 모델 학습 후 uncomment).
- `src/portfolio/config_loader.py` — `metalabeler.load_path` 부재 시 해당 entry 만 skip, 나머지는 정상 로드 (`on_metalabeler_missing="skip" | "raise"` 옵션, default skip).
- `src/live/loop.py:_load_orchestrator` — `RuntimeError` catch 부분 재검토 (graceful skip 방식 적용 시 RuntimeError 거의 없음).

### 2. KISMarketFeed (REST 1m polling) — 신규
- `src/live/feed_kis.py` — `MarketDataFeed` Protocol 구현. `connect()` 시 `KISClient` 구성, `subscribe(symbols)` 시 폴링 대상 등록, `__aiter__` 가 60초마다 `KISIntradayBar` 최신 분봉 조회 → 신규 분봉만 `Tick` 으로 yield.
- KRX 휴장일/장외 시간 가드: `universe.krx_calendar.is_krx_holiday` + `time(9, 0)` ~ `time(15, 30)` KST.

### 3. SnapshotBuilder (multi-timeframe ohlcv_history) — 신규
- `src/live/snapshot_builder.py` — `SnapshotBuilder.warmup(symbols)` 가 부팅 시 KIS REST 분봉 backfill (default: 1분봉 1000개 최근). 1분봉을 메모리에서 4h/15m/1h/EOD 로 resample.
- Tick 도착 시 1분봉 buffer update + resample dict 갱신. `build_snapshot(tick) -> dict` 가 `{ts, symbol, price, equity_krw, ohlcv_history: {symbol: DataFrame}, history: DataFrame, factors: {rsi: Series, ...}}` 반환.
- factor 선계산 (RSI 등) 은 `signals.compute` 의존; required_factors 합집합 계산 후 prefetch.

### 4. live/loop.py feed routing
- `run_shadow_loop` 에 `feed` 인자 자동 결정: symbol 이 6자리 숫자 (`05930` 패턴) → `KISMarketFeed`, 그 외 → `BinancePublicFeed`. `--feed` CLI 플래그로 강제 override 가능.
- `_load_orchestrator` 가 SnapshotBuilder 인스턴스 받아 보유. consumer loop 가 `tick → snapshot_builder.build_snapshot(tick) → orch.run_bar(ts, snapshot)`.

### 5. live_run.py 에 dashboard 백그라운드 task + WAL observer wiring (#181 통합)
- `--dashboard-port` (default 8000) 추가. 포트 0 이면 dashboard 비활성.
- `run_shadow_loop` 진입 직후 `DashboardState(wal_path=config.wal_path, timeline_broker=TimelineBroker())` 구성, `create_app(state)` → uvicorn 서버 백그라운드 task.
- `WAL(path, observer=lambda ev: state.timeline_broker.publish(asdict(ev)))` 로 wiring. WAL 쪽 observer 인자는 #181 머지로 이미 존재.
- shutdown 시 dashboard task cancel.

### 6. qta.spec hiddenimports 확장
- 추가: `src.dashboard`, `src.dashboard.app`, `src.dashboard.timeline_broker`, `src.dashboard.timeline_events`, `src.live.feed_kis`, `src.live.snapshot_builder`, `uvicorn`, `uvicorn.protocols`, `uvicorn.protocols.http`, `uvicorn.protocols.websockets`, `starlette.websockets`, `fastapi`, `prometheus_client`.

### 7. 테스트
- `tests/portfolio/test_config_loader.py` — metalabeler graceful skip 회귀
- `tests/live/test_feed_kis.py` — KIS REST polling 시 mock client 로 분봉 yield 검증
- `tests/live/test_snapshot_builder.py` — warmup + build_snapshot ohlcv_history shape
- `tests/live/test_loop_feed_routing.py` — symbol prefix → feed 선택 분기
- `tests/test_live_run_dashboard_wiring.py` — `--dashboard-port` 플래그 → dashboard task 기동, WAL observer broker.publish 호출
- `tests/packaging/test_qta_spec_includes_production_yaml.py` — qta.spec datas / hiddenimports 검증

### 8. 문서
- `docs/whitepaper/qta-master-plan-v01.md` §10-2 끝에 "production.yaml 위치" subsection 추가 — EXE 번들 경로 (`./configs/orchestrator/production.yaml`) + 사용자 override (`--production-yaml`) + metalabeler 활성화 절차.
- `configs/orchestrator/.ai.md` 신설 (없으면).
- `src/live/.ai.md` — feed_kis.py / snapshot_builder.py 추가 명시.
- `src/dashboard/.ai.md` — `wal_path` / `timeline_broker` runtime wiring 명시.

### 9. EXE 재빌드 + smoke
- `pyinstaller qta.spec` — Windows 단일 EXE.
- `dist/qta.exe --symbols 005930 --max-iterations 5 --broker paper-only` — KIS REST polling, 5전략 등록 확인 (logs), 신호 발생 확인 (WAL `signal_emitted` 이벤트).
- `dist/qta.exe --dashboard-port 8000` 별도 검증 — `/ws/timeline` 접속 가능 여부.

## 위험·결정
- **위험**: KIS REST polling 은 시장 열려있을 때만 동작. 폐장 중 smoke 시 5 iter 이내 tick 0개 가능 → mock feed 옵션 (`--feed mock`) 추가하여 smoke 테스트 결정성 확보.
- **결정**: metalabeler entry 는 commented 채로 두고 graceful skip 동작은 코드로 대비 (운영자가 uncomment 했을 때 모델 누락 → 그 한 줄만 skip).
- **결정**: ohlcv_history warmup 은 부팅 시 1회 KIS REST backfill 옵션 (a). 부팅 30~60s 지연 표시.

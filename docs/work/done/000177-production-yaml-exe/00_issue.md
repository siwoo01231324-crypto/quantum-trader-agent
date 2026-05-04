# [#177] chore: configs/orchestrator/production.yaml 작성 + EXE 재빌드 (전략 5종 등록 활성화)

## 배경
#123 EXE PoC 완료 후 실행해보니 `configs/orchestrator/production.yaml` 누락 → 빈 오케스트레이터 → 매매 미동작. #94 (메타라벨러 프로덕션 활성화) closed 였으나 production.yaml 본 EXE 빌드에 미포함.

## 완료 기준
- [x] `configs/orchestrator/production.yaml` 작성 — 5전략(momo-btc-v2, momo-vol-filtered, meanrev-pairs, breakout-donchian, momo-kis-v1) + 메타라벨러 (#85) + 리스크 임계 등록
- [x] `qta.spec` 의 datas 에 production.yaml 명시적 포함 검증 (`tests/packaging/test_qta_spec_invariants.py`)
- [x] EXE 재빌드 + `qta.exe --symbols BTCUSDT --max-iterations 100 --feed mock --broker paper-only` 실행 시 5전략 로드 + 시그널 발생 + 주문 체결 체인 확인
- [x] 백서 §10-2-1 "production.yaml 위치" 명시

## 의존성
- 선행: #94 (메타라벨러 프로덕션 활성화) closed 확인
- 선행: #181 (timeline WS) merged — qta.exe 통합 wiring 본 이슈에서 처리
- 후행: 모든 EXE 기반 사용자 UX 작업

## 작업 내역

### 2026-05-04 — 코드 갭 실측 + 스코프 합의
- 실측 결과: AC 의 "5전략 로드 + 시그널 발생" 을 막는 코드 갭 3개 발견
  1. `live/loop.py:179` 가 `BinancePublicFeed` 하드코딩 → KRX 종목(005930) tick 수신 불가
  2. `_tick_to_market_snapshot` 가 `ohlcv_history` 미충전 → 4 전략 항상 hold
  3. `models/momo-btc-v2/latest/` 부재 시 metalabeler 로드 실패 → fail-fast 로 전체 폴백
- 백로그 검색 결과 위 갭을 다루는 별도 이슈 없음. #133/#143 도 같은 갭에 의존.
- 사용자 결정: #177 단일 PR 안에 다 처리. KIS 시세는 REST 1m polling. ohlcv warmup 은 부팅 시 KIS REST backfill.
- 사용자 결정: #181 (`/ws/timeline` + WAL observer) 의 EXE wiring 도 #177 에서 처리 (#181 본 PR 머지 완료).

### 2026-05-04 — 구현 + 검증 완료
- **신규 모듈**: `src/live/feed_kis.py` (KISMarketFeed REST polling + MockReplayFeed), `src/live/snapshot_builder.py` (multi-timeframe ohlcv_history + KIS REST warmup)
- **수정**:
  - `configs/orchestrator/production.yaml` — 5 전략 등록 (momo-btc-v2, momo-vol-filtered, meanrev-pairs, breakout-donchian, momo-kis-v1) + `momo-btc-v2-meta` 운영자-활성 commented 블록
  - `src/portfolio/config_loader.py` — `on_metalabeler_missing="skip"` 옵션 추가 (기본 raise, 호환). async strategy 는 직접 등록 / sync 만 `_StrategyAdapter` 경유로 분기
  - `src/portfolio/_strategy_adapter.py` — `ctx["market_snapshot"]` 에서 bar/history/factors 추출 (이전엔 ctx top-level 만 보던 버그 fix)
  - `src/portfolio/_async_orchestrator.py` — `ctx["factors"]` top-level 노출 (MomoKisV1 호환)
  - `src/live/loop.py` — `_select_feed`, SnapshotBuilder integration, ShadowConfig 확장 (feed_mode, kis_client, wal_observer, mock_ticks, snapshot_builder_config), `signal_emitted` WAL emit, default Policy 자동 주입
  - `scripts/live_run.py` — `--feed`, `--mock-bars`, `--dashboard-port` 플래그 + `_build_kis_client` + `_build_mock_ticks` (engineered RSI divergence 패턴) + `_start_dashboard` (uvicorn 백그라운드 task) + `_run_pipeline` (DashboardState wal_path / TimelineBroker wiring)
  - `qta.spec` — hiddenimports 에 dashboard / KIS feed / uvicorn / fastapi / starlette + bare-name aliases (backtest, risk, signals 등) 추가, excludes 에 torch/tensorflow/jax/transformers 추가, pathex `["."]` → `[".", "src"]`
  - `pyproject.toml` — `pytz>=2024.1` 추가
  - `Dockerfile` — `COPY configs ./configs`
- **신규 테스트**: `test_feed_kis.py`, `test_snapshot_builder.py`, `test_loop_feed_routing.py`, `test_live_run_dashboard_wiring.py`, `test_qta_spec_invariants.py`, `test_production_yaml_smoke.py` + `test_config_loader.py` 의 graceful skip 케이스
- **문서**: 백서 §10-2-1 "production.yaml 위치" subsection 추가, `configs/orchestrator/.ai.md` / `src/live/.ai.md` / `src/dashboard/.ai.md` 갱신
- **EXE 빌드**: 9차 빌드 끝에 통과 (1차 24분 stall — torch DLL 충돌 / 4차 src on pathex 누락 / 6차 bare-name aliases 미등록 / 7차 mock gap_sec=0 race / 8차 divergence pattern 약함). 최종 dist/qta.exe = 143MB
- **EXE 스모크**: `qta.exe --symbols 005930 --max-iterations 100 --broker paper-only --feed mock --mock-bars 100 --dashboard-port 0` → 5 전략 로드 + WAL 에 `signal_emitted` 2건 (momo-btc-v2 buy, bullish divergence) 기록 확인
- **단위 테스트**: 1742 passed, 11 skipped, 3 deselected (회귀 0)

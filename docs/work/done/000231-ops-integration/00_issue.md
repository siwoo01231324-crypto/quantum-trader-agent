# feat: 운영 인프라 통합 리팩토링 — 11전략 실가동 + AC0 검증 게이트 + Lake 누적 (#105/#218/#227 후속)

# 운영 인프라 통합 리팩토링 — 11전략 실가동 + AC0 검증 게이트 + Lake 누적

## 배경 (비판적 진단)

#105 KIS Phase 2 운영 데몬 가동 4일째 (5/11~5/14) **paper WAL 발주 0건, AC 0/6 FAIL**. 진단 결과 root cause 6가지:

1. **`live_run.py --broker`** 가 KIS 전용 (`paper-only/kis-paper/kis-paper-shadow`) → Binance 시세 받는 전략 (momo-btc-v2, momo-vol-filtered, meanrev-pairs) 활성 불가능.
2. **`OrderRouter`** 가 단일 active broker (#112 issue body 인용) → multi-broker 동시 운영 미구현.
3. **production.yaml 의 `cs-*` 6 universe-scan 전략**: "broker 미연동 → graceful hold" 코드 본인 주석으로 무력 인정.
4. **KIS REST polling 3종목 한정** → KOSPI200 전체 시세 불가능. WS 코드 (#227 `KISWebSocketMarketFeed`) 만들었지만 production wire 0.
5. **`bench_live_scanner.py`** 가 per-bar `panel.iloc[:i+1]` O(N²) loop → 1m bar 5y 검증 4시간 30분 (CI 자동화 불가능).
6. **AC2~6 모두 "발주 발생" 전제로 측정** → AC0 (dispatch 1회라도 발생) 게이트 부재.
7. **KIS 1m lake**: `get_pool_codes(seed=42)` deterministic 30종목 + 매일 같은 partition 덮어쓰기 → 350종목 × 90일 누적 불가능. 현재 누적 0.1%.

→ 각 PR 이 단위 테스트 + nightly smoke 만으로 머지되고 integration end-to-end 검증을 "후속 운영" 으로 미룬 패턴이 #79 → #218 → #227 모두 동일하게 반복.

## 목표

11개 등록 strategy 모두 실제로 시세 받고 신호 발화 + paper 발주까지 도달. 24시간 안에 검증 가능. 350종목 × 90거래일 백테스트 데이터 확보.

## 변경 범위

### S1: Binance shadow live-daemon

- `scripts/live_run.py --broker` 에 `binance-testnet-shadow` 추가
- `AsyncOrderRouter` 를 single-active → multi-broker registry 로 확장 (per-symbol routing)
- `docker-compose.live.yml` 에 `qta-live-daemon-binance` 새 서비스 (`--symbols=BTCUSDT,ETHUSDT,ETHBTC,SOLUSDT,...`)
- 영향: momo-btc-v2 / momo-vol-filtered / meanrev-pairs (3 전략) 활성

### S2: cs-* universe broker 연동

- `SnapshotBuilder` 가 `kis/universe_quote.fetch_universe_snapshot` + `binance/universe_quote.fetch_universe_klines` 호출하도록 wire
- `cs_async_wrapper` 의 "graceful hold" 분기 제거 (실 데이터 들어오면 dispatch)
- `production.yaml` cs-* 6개 주석 정리
- 영향: cs-tsmom-kr / cs-rsi-div-kr / cs-adx-ma-kr / cs-tsmom-crypto / cs-rsi-div-crypto / cs-macd-vol-crypto (6 전략) 활성

### S3: KIS WS production wire-up

- `KISWebSocketMarketFeed` (#227 코드) production 진입점에 연결
- KOSPI200 200종목 stagger subscribe (KIS WS 제한: 동시 40 종목 → 5 batch rotation 또는 다중 connection)
- 영향: breakout-donchian (1 전략) 활성 + tracking_error 측정 정확도 ↑ (REST polling 대비 100x 실시간성)

### S4: bench 알고리즘 가속

- per-bar `panel.iloc[:i+1]` → 전체 panel indicator vectorize (`compute_rsi(panel)`, `compute_macd(panel)` 한 번)
- entry signal mask 추출 → 진입 시점만 trade simulation (O(N²) → O(N))
- `multiprocessing.Pool` 으로 종목별 병렬 (8 코어 → 8x 가속)
- 효과: 4h 30min → **5분 이내**

### S5: WAL `strategy_evaluated` event

- `AsyncStrategyOrchestrator.run_bar` 마다 `strategy_evaluated{strategy_id, symbol, ts, decision: hold/buy/sell, reason}` event WAL append
- 운영 디버깅 가시화: "on_bar 호출 0건" vs "호출됐지만 hold" 구분 가능

### S6: 운영 디버깅 도구

- dispatch 카운트 섹션 추가 (strategy_evaluated event 카운트)
- `/api/run/status` 에 per-strategy `dispatch_total` 노출
- `universe-rebal-cron` stdout 손실 fix (logging driver `max-size: 10m` 또는 `>> /data/logs/universe-rebal.log` redirect)
- `daily_check_kis.ps1` 에 lake coverage 섹션 추가 (S7-4 와 연동)

### S7: KIS 1m lake 누적 전략 전면 재설계

#### S7-1: universe 확장 (30 → 350)

- `get_pool_codes(seed=42)` deterministic 30 → 전체 KOSPI200 + KOSDAQ150 (350)
- `--n-pool 350` 또는 신규 `--universe full` 옵션
- rate-limit 분산: 350종목 × 0.6s sleep = 약 3분 30초 (KIS 6 req/s 안)

#### S7-2: parquet write 모드 변경 (overwrite → append + dedup)

- 파티션: `year=YYYY/month=MM/symbol=<code>/part-0.parquet`
- → `year=YYYY/month=MM/day=DD/symbol=<code>/part-0.parquet` (day 추가)
- 또는 ts 기준 dedup (같은 ts row 발견 시 skip)
- 기존 lake migration 스크립트 작성 (한 번 실행)

#### S7-3: rolling backfill

- 매일 16:00 cron: 당일 1거래일 (이미 작동)
- 신규 cron: 22:00 KST 과거 30일 backfill 분산 → 매일 350종목 × 1일씩 누적
- 12일 안에 350종목 × 12일 누적 (1m bar 백테스트 가능 임계)
- 90일 운영 → 350종목 × 90일 = #153 KIS 가설 본판정 데이터 확보

#### S7-4: lake monitor 확장

- `kis_lake_monitor.py` 확장: 종목별 누적 거래일 카운트 + 검색식 universe (저가주 900~10,000원 구간) 종목 수 표시
- `daily_check_kis.ps1` 에 "lake coverage" 섹션 추가 (S6 과 연동)

---

## AC0 — 머지 게이트 (이거 통과 못하면 머지 금지)

**핵심 absolute gate** — 이전 PR 들이 모두 빠뜨린 검증:

- **AC0_dispatch_proof**: docker compose up 후 24시간 안에 paper WAL `order_submitted` event ≥ 1건 발생 (실측)
- **AC0_broker_coverage**: `/api/account/info` 응답에 `kis.ok=true` + `binance.ok=true` 동시 + 각 broker 별 `warmup_loaded` ≥ 1
- **AC0_strategy_dispatch**: 11개 strategy 모두 `strategy_evaluated` event ≥ 1 발생 (S5 이벤트 활용)

## AC1~AC9 — 영역별 검증

- **AC1** Binance shadow daemon: `qta-live-daemon-binance` healthy + BTCUSDT/ETHUSDT/ETHBTC warmup 391 bars 적재
- **AC2** Universe broker wire: cs-* 6 strategy 모두 strategy_evaluated event 발생 (S5)
- **AC3** WS production: KOSPI200 200종목 중 195+ 종목 tick 수신 (5분 통계, missing 비율 < 3%)
- **AC4** bench 가속: `bench_live_scanner.py --all --bar 1m` < 10분 종료 (CI 머지 게이트화 가능)
- **AC5** strategy_evaluated WAL: 단위 테스트 + 실 데몬 24시간 운영 후 event ≥ 1000
- **AC6** daily_check_kis.ps1: dispatch 카운트 + lake coverage 섹션 표시 + universe-rebal docker logs > 0 lines
- **AC7** 운영 검증: 머지 후 72시간 안에 paper WAL `order_filled` event ≥ 5건 (#133 AC3 의 5% 수준 selspot)
- **AC8** lake 누적 검증: 머지 후 12일 안에 `symbols >= 350 AND distinct_days >= 12`, 90일 안에 `distinct_days >= 90`, 검색식 universe (가격 필터 통과) 종목 수 >= 50
- **AC9** 데이터 보존 무결성: 같은 `(symbol, ts)` row 가 lake 에 1건만 (dedup 검증 테스트), 백테스트 시 lake.read_parquet 한 번에 350종목 × 90일 = 약 1.3억 rows 로드 가능 (memory < 16 GB)

---

## 의존성

- ✅ #228 (live-scanner paradigm) 머지 후 시작 — 진행 중 bench 종료 + 머지 대기
- #105 (Phase 2 KIS paper), #133 (Phase 2 operation), #218 (universe-scan), #226 (universe-rebal cron), #227 (live-scanner) 작업물 위에 build

## 영향 파일 (예상)

- `scripts/live_run.py` (broker 옵션 + multi-router) — S1
- `src/brokers/router.py` (multi-active registry) — S1
- `src/live/loop.py` (multi-broker dispatch) — S1
- `src/live/snapshot_builder.py` (universe_quote wire) — S2
- `src/live/feed_kis_ws.py` (production wire entrypoint) — S3
- `src/portfolio/_async_orchestrator.py` (strategy_evaluated event) — S5
- `scripts/bench_live_scanner.py` (vectorize + multiprocessing) — S4
- `scripts/cron_fetch_kis_daily.py` + `kis_1m_fetch_loop.sh` (universe 350 + append) — S7-1, S7-2, S7-3
- `src/universe/krx_pool.py` (deterministic 30 → 350 옵션) — S7-1
- `scripts/kis_lake_monitor.py` (coverage 확장) — S7-4
- `docker-compose.live.yml` (binance daemon 서비스 + universe-rebal logging fix) — S1, S6
- `configs/orchestrator/production.yaml` (cs-* 주석 정리) — S2
- `daily_check_kis.ps1` (dispatch + lake coverage 섹션) — S6, S7-4
- 신규 테스트: `tests/integration/test_dispatch_proof.py`, `tests/live/test_multi_broker_router.py`, `tests/bench/test_vectorized_replay.py`, `tests/integration/test_lake_dedup.py`

**예상 변경량**: 50-60 파일, ~3000-4000 LOC. 작업 기간 **1.5-2주**.

## 라벨

`feat`, `ops`, `integration`, `architecture`, `phase2-blocker`


## 작업 내역

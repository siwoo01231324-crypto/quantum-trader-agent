# [#231] 운영 인프라 통합 리팩토링 — 구현 계획

> 작성: 2026-05-14
> 의존: PR #228 머지 완료 (e541204)

---

## 완료 기준

### AC0 — 머지 게이트 (절대 게이트)
- [ ] AC0_dispatch_proof: docker compose up 후 24h 안에 paper WAL `order_submitted` ≥ 1
- [ ] AC0_broker_coverage: `/api/account/info` 에 `kis.ok=true` + `binance.ok=true` 동시 + 각 broker `warmup_loaded` ≥ 1
- [ ] AC0_strategy_dispatch: 11 strategy 모두 `strategy_evaluated` event ≥ 1

### AC1-AC9 — 영역별 검증
- [ ] AC1: `qta-live-daemon-binance` healthy + BTCUSDT/ETHUSDT/ETHBTC warmup 391 bars
- [ ] AC2: cs-* 6 strategy 모두 strategy_evaluated event 발생
- [ ] AC3: KOSPI200 200종목 중 195+ 종목 WS tick 수신 (5분 통계, missing < 3%)
- [ ] AC4: `bench_live_scanner.py --all --bar 1m` < 10분 종료
- [ ] AC5: 24h 운영 후 strategy_evaluated event ≥ 1000
- [ ] AC6: daily_check_kis.ps1 dispatch + lake coverage 섹션 표시 + universe-rebal docker logs > 0 lines
- [ ] AC7: 머지 후 72h 안에 paper WAL `order_filled` ≥ 5
- [ ] AC8: 12일 안에 lake `symbols >= 350 AND distinct_days >= 12`, 90일 안에 `distinct_days >= 90`, 검색식 universe 종목 수 >= 50
- [ ] AC9: 같은 `(symbol, ts)` row 1건만 (dedup), 350×90일 panel = 1.3억 rows < 16 GB memory

---

## 구현 순서 — 효과 큰 순 + 의존성 정렬

```
Phase 1 (운영 가시화): S5 → S6 → S4
Phase 2 (시세 확장):   S1 → S3
Phase 3 (전략 dispatch): S2
Phase 4 (데이터 누적): S7
Phase 5 (AC0 검증):    24h paper run + AC 통과 증명
```

### 의존성 graph
```
S5 (WAL event)  ←  AC0/AC5 측정 기반
  ↓
S6 (운영 도구)
  ↓
S4 (bench 가속) — independent
S1 (Binance daemon) ─┐
                      ├→ S2 (universe wire)
S3 (KIS WS)          ─┘
                      ↓
                      S7 (lake 누적, S1+S2 위에 build)
```

---

## Phase 1: 운영 가시화 (1-2일)

### S5: WAL `strategy_evaluated` event

**변경 파일**:
- `src/portfolio/_async_orchestrator.py` (`run_bar` 안에 event emit)
- `src/live/loop.py` (WAL append seam 활용)
- `tests/portfolio/test_strategy_evaluated_event.py` (신규)

**구현**:
1. `_async_orchestrator.py:run_bar` 의 strategy fan-out 안에서 매 strategy 평가 후:
   ```python
   self._wal.append({
       "event_type": "strategy_evaluated",
       "schema_version": 1,
       "ts": now_utc(),
       "payload": {
           "strategy_id": sid,
           "symbol": tick.symbol,
           "decision": "hold|buy|sell",
           "reason": "rsi=28.5,vol=2.1x" (또는 "no_data"),
       }
   })
   ```
2. 단위 테스트: synthetic tick 4개 → WAL 에 4 events 확인

**검증**: AC5 (24h 운영 후 ≥ 1000), AC0_strategy_dispatch

### S6: 운영 디버깅 도구

**변경 파일**:
- `daily_check_kis.ps1` (이미 작업, dispatch 섹션만 추가)
- `docker-compose.live.yml` (universe-rebal-cron `logging` driver 추가)
- `universe_rebal_loop.sh` (echo redirect 또는 stdout flush)

**구현**:
1. `daily_check_kis.ps1` 에 신규 섹션:
   ```
   === Dispatch (S5) ===
     strategy_evaluated (24h):  <N>
     by-strategy breakdown
   ```
2. docker-compose.live.yml 의 universe-rebal-cron:
   ```yaml
   logging:
     driver: json-file
     options:
       max-size: 10m
       max-file: 3
   ```
3. universe_rebal_loop.sh: `python ... 2>&1 | tee /data/logs/universe-rebal.log` 추가

**검증**: AC6

### S4: bench 알고리즘 가속

**변경 파일**:
- `scripts/bench_live_scanner.py` (vectorize)
- `tests/bench/test_vectorized_replay.py` (신규)

**구현**:
1. `_replay_symbol` 의 per-bar loop → 전체 panel indicator vectorize
2. entry signal mask 추출 → 진입 시점만 trade simulation
3. multiprocessing pool 은 이미 있음 (PR #228), vectorize 추가만
4. 벤치: 1m bar 30 종목 × 5 strategy < 10분

**검증**: AC4

---

## Phase 2: 시세 확장 (3-5일)

### S1: Binance shadow live-daemon

**변경 파일**:
- `scripts/live_run.py` (`--broker` choices 확장)
- `src/brokers/router.py` (multi-broker registry)
- `src/live/loop.py` (per-symbol broker routing)
- `docker-compose.live.yml` (`qta-live-daemon-binance` 신규 서비스)
- `tests/live/test_multi_broker_router.py` (신규)
- `tests/live/test_binance_shadow_smoke.py` (신규)

**구현**:
1. `live_run.py`:
   ```python
   choices=["paper-only", "kis-paper", "kis-paper-shadow",
            "binance-testnet-shadow"]
   ```
2. `OrderRouter` 단일 active → registry:
   ```python
   class MultiBrokerRouter:
       def __init__(self, brokers: dict[str, Broker]):  # {"kis": ..., "binance": ...}
           ...
       def route(self, symbol: str) -> Broker:
           if symbol.endswith("USDT") or "BTC" in symbol:
               return self._brokers["binance"]
           return self._brokers["kis"]
   ```
3. docker-compose.live.yml 새 서비스:
   ```yaml
   qta-live-daemon-binance:
     command:
       - "scripts/live_run.py"
       - "--broker=binance-testnet-shadow"
       - "--symbols=BTCUSDT,ETHUSDT,ETHBTC,SOLUSDT,..."
       - "--schedule=always"
   ```

**검증**: AC1, AC0_broker_coverage

### S3: KIS WS production wire-up

**변경 파일**:
- `src/live/feed_kis_ws.py` (production entrypoint)
- `src/live/loop.py` (WS feed 선택 옵션)
- `scripts/live_run.py` (`--feed kis-ws` 추가)
- `tests/live/test_feed_kis_ws_subscribe.py` (확장)

**구현**:
1. `KISWebSocketMarketFeed` 의 single-connection 200 종목 subscribe
2. KIS WS 동시 40 종목 제한 → 5 connection pool 또는 rotation
3. 자동 reconnect (이미 코드 있음, wire 만)

**검증**: AC3 (KOSPI200 195+ 종목 tick 수신)

---

## Phase 3: 전략 dispatch (3-5일)

### S2: cs-* universe broker 연동

**변경 파일**:
- `src/live/snapshot_builder.py` (universe quote fetch wire)
- `src/backtest/strategies/cs_async_wrapper.py` (graceful hold 분기 제거)
- `configs/orchestrator/production.yaml` (cs-* 주석 정리)
- `tests/integration/test_cs_universe_live_dispatch.py` (신규)

**구현**:
1. `SnapshotBuilder.build()` 가 `symbol == "KRX_TOP350_BASKET"` 일 때:
   ```python
   from src.brokers.kis.universe_quote import fetch_universe_snapshot
   ohlcv = fetch_universe_snapshot(kis_client, universe=KOSPI200+KOSDAQ150)
   ```
2. 동일하게 Binance 측: `fetch_universe_klines`
3. cs_async_wrapper 의 `if "ohlcv_history" not in snapshot: return hold` 제거

**검증**: AC2 (cs-* 6 strategy 모두 strategy_evaluated 발생)

---

## Phase 4: 데이터 누적 (3-5일)

### S7: KIS 1m lake 누적 전략

**변경 파일**:
- `src/universe/krx_pool.py` (deterministic 30 → 350 옵션)
- `scripts/cron_fetch_kis_daily.py` (append + dedup)
- `scripts/kis_1m_fetch_loop.sh` (rolling backfill)
- `scripts/kis_lake_monitor.py` (coverage 확장)
- `scripts/migrate_lake_to_dayparted.py` (1회 실행 마이그레이션)
- `tests/integration/test_lake_dedup.py` (신규)

**구현**:
1. `krx_pool.py` 에 `get_full_universe()` 추가 (KOSPI200 + KOSDAQ150 = 350)
2. cron_fetch_kis_daily.py:
   - parquet path: `year/month/day/symbol/part-0.parquet` (day 추가)
   - 또는 `(symbol, ts)` dedup 후 append
3. `--universe full` 옵션 + rate-limit 분산
4. kis_1m_fetch_loop.sh 의 N_POOL=30 → 350
5. 신규 cron 22:00 KST backfill 30일 분산
6. lake monitor: 종목별 누적 거래일 + 검색식 universe 가격 필터 종목 수

**검증**: AC8, AC9

---

## Phase 5: AC0 통합 검증 (1-2일)

24h 운영 + AC 측정:

```bash
# 1. 통합 PR docker compose up
docker compose -f docker-compose.live.yml up -d

# 2. 24h 운영 후 검증
python scripts/verify_ac0.py --hours 24
# → AC0_dispatch_proof, AC0_broker_coverage, AC0_strategy_dispatch 측정

# 3. 통과 시 PR 머지 가능
```

**verify_ac0.py 신규 스크립트**:
- WAL 분석 (`order_submitted` 카운트, `strategy_evaluated` 카운트)
- dashboard `/api/account/info` 호출
- 결과 JSON 출력 + telegram 알림

---

## Guardrails

### Must Have
- 머지 전 AC0 3개 모두 통과 (절대 게이트)
- 모든 신규 코드 단위 테스트 첨부
- `production.yaml` 변경 시 dry-run 검증 (config_loader smoke test)
- WAL schema version 변경 시 backward-compatible (replay 가능)
- 모든 strategy `enabled: false` 로 머지 (운영 시작 별도 결정)

### Must NOT Have
- 실 자금 거래 자동 켜기 (KIS_PAPER=false 변경 금지)
- 단위 테스트 skip 또는 `xfail` 누적
- 머지 직전 large refactoring 없이 큰 PR 만들기 (각 phase 별 sub-commit 권장)
- bench 의 trade 결과를 production decision 기준으로 단독 사용 (forward test 병행)

---

## 일정 추정

| Phase | 작업 | 소요 |
|---|---|---|
| 1 | S5 + S6 + S4 | 2일 |
| 2 | S1 + S3 | 3-4일 |
| 3 | S2 | 3-4일 |
| 4 | S7 | 3-4일 |
| 5 | AC0 검증 | 1-2일 (대기 포함) |
| **합계** | **1.5~2주** |

---

## 위험 / 완화

- **KIS WS 동시 40 종목 제한** → 5 connection pool 또는 rotation. 코드 복잡도 증가.
- **multi-broker router** → 기존 단일 broker 가정 코드 (paper_broker, pnl_aggregator 등) 전체 점검 필요. 회귀 위험.
- **lake migration** → 5/14 기존 part-0.parquet 30종목을 day-partitioned 로 1회 이동. 손실 방지 위해 dry-run + backup.
- **universe-scan dispatch** → cs-* 가 즉시 발주하면 5/15 (금) 15:32 KST 의 universe-rebal-cron 과 충돌 가능. 운영 시작 시 universe-rebal-cron 만 활성, live-daemon 의 cs-* dispatch 는 `enabled: false`.

---

## 다음 단계

1. **즉시**: S5 구현 시작 (가장 빠른 운영 가시화)
2. S5 PR sub-commit → S6 → S4 순으로 Phase 1 마무리
3. Phase 1 완료 후 사용자 검토 → Phase 2 진행 결정

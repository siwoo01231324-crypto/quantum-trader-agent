---
type: spec-architecture
id: airborne-fire-driven-consume
name: 에어본 발화 직접구동 consume (봉루프 decouple + 도착시각 게이트)
status: in-progress
owner: siwoo
created: 2026-06-11
---

# 에어본 발화 직접구동 consume

## 배경 (사고 — 2026-06-11 "7시 롱 미매수")

연속가동 중인데도 **07:00 KST(22:00 UTC) 봉만 진입 0건**. 원인:

- consume 이 `history.jsonl` 발화는 직접 읽지만, **"언제 평가하나"는 트레이더의
  OHLCV 봉루프(`orchestrator.run_bar` → live-scanner per-symbol on_bar)에 종속.**
- `run_bar` 은 `_universe_ohlcv`(트레이더 스냅샷)에 있는 종목만 평가 → universe
  refresh 랙/스냅샷 갭으로 그 봉에 해당 종목이 스냅샷에 없으면 **영영 미평가** →
  발화는 history.jsonl 에 있는데 진입 안 됨.
- 검증: 22:00 UTC 발화 12건(SOL/OPENAI/SUI/DOGE/LINK/AVAX 롱) 전부 history.jsonl
  에 존재. 그러나 트레이더 22:00 UTC 활동 0건. 4·8·10시는 정상 진입(봉루프가
  그 봉을 안 건너뜀).

## 설계 원칙

1. **발화가 곧 진입 트리거** — 트레이더 봉루프와 무관하게 history.jsonl 발화를
   직접 구동한다. 봉 스냅샷 랙이 발화를 떨어뜨리지 못한다.
2. **도착시각 게이트 = 봉마감시각 = 알림시각** — `floor(fire_ts, 1h).KST.hour`
   를 게이트 집합에 대조. "봉 시작/마감" 개념 제거(혼선 차단). 발화가 7시에 오면
   `7 ∈ {1,2,3,6,7,8,23}` → 매수. **매수가 정확히 게이트 시각에 일어난다.**
3. **OHLCV 불필요** — side 는 발화에서, 게이트 시각은 fire_ts 에서, 진입가는
   fire_close 에서. 종목 OHLCV 없이 진입 가능(BTC trend filter 만 BTC OHLCV 사용).

## 컴포넌트

### `src/live/airborne_fire_consumer.py` (신규)
`AirborneFireConsumer` — 백그라운드 task (봉루프와 별개).

```
sweep_once():
  fires = store.load_since(now - FRESHNESS)          # 기본 FRESHNESS=10분
  for f in fires (ts 오름차순):
    bar_close = floor(f.ts, 1h)                       # = 알림시각
    bar_open  = bar_close - 1h                        # dedup 키(on_bar consume 과 동일)
    hour_kst  = bar_close.tz_convert(KST).hour
    if now - f.ts > FRESHNESS: continue               # 오래된 발화 진입 금지(재시작 재매수 차단)
    for strat in airborne_strategies:                 # kst-hours, short-whitelist
      if hour_kst not in strat.kst_entry_hours: continue
      if f.side not in strat.allowed_sides: continue  # kst-hours={long,short}, short-wl={short}
      if strat.universe and f.symbol not in strat.universe: continue
      if f.side == 'long' and strat.btc_filter and btc_downtrend(): continue
      intent = orchestrator.dispatch_fire_entry(
                  strat.id, f.symbol, f.side, price=f.fire_close, ts=f.ts)
      if intent: route_order_intents([intent]); mark_dedup(strat, f.symbol, bar_open)

run_loop(stop_event): SWEEP_INTERVAL(기본 15초)마다 sweep_once, 절대 raise 안 함.
```

- **dedup**: 기존 `logs/airborne_reentry/{ClassName}.json` 공유. 키=symbol, 값=
  str(bar_open) — on_bar consume 과 동일 키라 양쪽 동시가동해도 중복진입 0. 추가로
  orchestrator `_live_entered` 가 (sid,symbol) 당 1포지션 보장.
- **freshness**: now−fire_ts ≤ 10분만 진입. 재시작 시 backlog 전체 재매수 차단 +
  consumer 가 잠깐(<10분) 죽었다 살아나면 그 사이 발화는 따라잡음.

### `AsyncStrategyOrchestrator.dispatch_fire_entry()` (신규)
단일 발화 진입 — `run_bar` 의 진입 로직 재사용(중복 없이 추출):
`_live_entered` dedup → stop cooldown → max_concurrent cap → `resolve_size`/
`size_to_qty`(price=fire_close, venue equity) → policy `evaluate` → preset TP/SL
meta(stop_loss_pct/take_profit_pct) → `OrderIntent` 반환(없으면 None). `_on_entry`
dynamic stop/TP 콜백도 동일 호출. WAL `strategy_evaluated` emit.

### live loop 배선 (`src/live/loop.py`)
`AIRBORNE_FIRE_CONSUMER=1` (신규 env) 시:
- `AirborneFireConsumer` 구성 + task 시작 (fire store, orchestrator, order router =
  run_bar OrderIntent 라우팅과 동일 경로, btc_ohlcv_provider = 스냅샷캐시 BTC,
  equity_provider).
- on_bar consume 단락: 새 consumer 활성 시 strategy 의 on_bar consume 분기는 hold
  반환(이중경로 혼선 제거). `_live_entered`+dedup 로 안전하지만 명시적으로 끈다.

### 텔레그램 데몬 (`scripts/airborne_alert_daemon.py`)
v0.6.51 의 buy-time +1 시프트 **되돌림**. 트레이더와 동일하게:
- 게이트 판정 = `floor(fire_ts,1h).KST.hour ∈ {1,2,3,6,7,8,23}` (도착시각).
- 표시 = 도착시각(=알림시각) + 집합 `{1,2,3,6,7,8,23}` 그대로. (혼란스럽던
  `{0,2,3,4,7,8,9}` 표기 제거.)

## 게이트 의미 변경 (5y 검증 필요)

기존: 봉 *시작*시각 게이트 → 매수 시작+1h({2,3,4,7,8,9,0}). 신규: 봉 *마감/도착*
시각 게이트 → 매수 정확히 {1,2,3,6,7,8,23}. **1시간 이동 = 백테스트와 어긋남.**
운영자 판단으로 적용, 5y walk-forward 재검증 전까지 모니터링(동일 in-sample caveat).

## 검증
- `tests/live/test_airborne_fire_consumer.py`: 게이트(도착시각)/side필터/freshness/
  dedup/BTC필터/dispatch 호출 — synthetic fire 로.
- `dispatch_fire_entry` 단위테스트: sizing/_live_entered/cooldown/preset meta.
- 회귀: 기존 on_bar consume 테스트 무영향(env off 시 byte-identical).

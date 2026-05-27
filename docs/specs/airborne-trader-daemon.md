---
type: spec-architecture
id: airborne-trader-daemon
name: Standalone Airborne Trader Daemon — daemon log listener → broker (orchestrator 통과 X)
status: skeleton
owner: siwoo
created: 2026-05-27
updated: 2026-05-27
tags:
- airborne
- standalone-trader
- event-driven
- skeleton
---

# Standalone Airborne Trader Daemon

## 도입 배경

[[live-airborne-bb-reversal-kst-hours]] (PR #327) 는 BINANCE_USDT_TOP30
universe 한정 자동매매. 그러나 사용자 요구는 **qta-airborne-daemon 의 Telegram
알림 전 종목 (top-100 동적)** 을 자동매매. 기존 orchestrator 는 *symbol-fixed
bar dispatch* 패턴 (사전 등록 universe) 이라 daemon 의 *동적 universe* 와
패러다임 불일치 → orchestrator 안에서 통합하려면 Signal protocol + broker
executor + risk policy 전체 수술 필요.

본 spec 은 그 대신 **독립 trader process** (`scripts/airborne_trader_daemon.py`)
를 정의한다. orchestrator 통과 X, 자체 risk + 자체 broker + 자체 WAL.

## 아키텍처

```
qta-airborne-daemon (기존, 무수정)
  └─ docker stdout FIRE 라인
       ↓ (docker logs polling)
qta-airborne-trader (신규 standalone)
  ├─ AirborneFireListener (src/live/airborne_fire_listener.py)
  │   └─ docker logs --since <Z> + parse + dedup
  ├─ AirborneTraderConfig (env + defaults)
  ├─ AirborneTraderState (SQLite WAL: positions + fires_processed)
  ├─ AirborneTraderRisk (6-gate 검증)
  │   1. KST hour ∈ {8, 11, 16, 22}
  │   2. fire ts age ≤ 5분 (stale skip)
  │   3. max_concurrent_positions ≤ 10
  │   4. same-symbol 차단 (1 종목 1 포지션)
  │   5. cooldown 900s after stop_loss
  │   6. daily loss limit -200 USDT (KST 자정 기준)
  ├─ BrokerInterface (Protocol)
  │   ├─ DummyBroker (현 PR — dry-run / 테스트 용)
  │   └─ BinanceFuturesBroker (후속 PR — 실 발주)
  └─ AirborneTrader (async main loop)
      ├─ handle_fire: dedup → risk → broker.place → state.open
      └─ monitor_positions: mark_price → stop/TP 도달 시 close
```

## cs-tsmom 등 다른 entity 와 격리

- **별도 docker container**: cs-tsmom + live-airborne-bb-reversal-kst-hours
  는 qta.exe / orchestrator 안에서 동작. 본 trader 는 *완전 독립* process.
- **별도 broker session**: Binance API key 는 같지만 자체 client. account
  balance 는 *공유* (Binance 한 계정) — 동시 사용 시 capital 경합 가능.
- **별도 WAL**: `logs/airborne_trader/state.db` (SQLite). orchestrator WAL
  (`logs/live/{run_id}/wal.jsonl`) 과 분리.

## 자본 경합 회피

| 전략 | capital_fraction | 동시 노출 한도 |
|---|---|---|
| cs-tsmom-crypto-daily (orchestrator) | 0.5 (잔고 50%) | 10 종목 × 5% |
| live-airborne-bb-reversal-kst-hours (orchestrator, PR #327) | (default_size 0.05) | 종목당 ~$200 × 동시 N |
| **airborne_trader_daemon (본 spec)** | hardcoded `position_usd=200` | **max 10 종목 × $200 = $2000** |

3 entity 합산 시 같은 계정 잔고 경합. 운영 권장:
- cs-tsmom: 잔고의 50%
- live-airborne-kst-hours: 잔고의 25% (자동 — orchestrator capital_fraction 미적용)
- airborne_trader_daemon: 잔고의 25% (= $2000 / 잔고)

## 본 PR scope (skeleton)

✅ 완료:
- `src/live/airborne_fire_listener.py` — 이미 PR #327 / 본 PR
- `src/live/airborne_trader/__init__.py` — package
- `src/live/airborne_trader/config.py` — env + defaults
- `src/live/airborne_trader/state.py` — SQLite WAL
- `src/live/airborne_trader/risk.py` — 6-gate
- `src/live/airborne_trader/trader.py` — async loop + DummyBroker
- `scripts/airborne_trader_daemon.py` — entry point
- `tests/live/airborne_trader/test_config.py` + test_state + test_risk + test_trader

❌ 후속 PR 작업 (실거래 활성화 전):
- `src/live/airborne_trader/brokers/binance_futures.py` — 실 Binance Futures
  client (ccxt or aiohttp 기반)
- daily loss alert (Telegram webhook)
- mark price 모니터링 최적화 (현재는 매 cycle 마다 모든 open position 의
  get_mark_price 호출 — 100 종목이면 100 REST 호출/30s)
- docker-compose.live.yml entry — qta-airborne-trader service 추가
- 운영 가이드 + 첫 실 발주 체크리스트
- 6개월 paper monitoring → 실거래 ramp

## 운영 위험 분석 (실거래 활성화 전 검토 필수)

1. **In-sample selection bias**: KST {8,11,16,22}시 게이트는 BINANCE_USDT_TOP30
   5y 데이터로 cherry-pick. top-100 universe 의 새 70 종목에선 검증 X.
2. **daemon ↔ trader 시간 race**: daemon FIRE 발생 ↔ trader poll 까지 30s 지연.
   변동성 큰 알트는 그 사이 가격이 진입 가격에서 멀어질 수 있음.
3. **REST get_mark_price race**: open position N 개 × 매 30s = 100 REST/30s.
   rate limit (Binance fapi 2400 req/min) 안에 들어가지만 다른 entity (cs-tsmom)
   와 같은 API key 공유 시 합산.
4. **Crash recovery**: SQLite WAL 로 positions 복원하지만, broker 측 실제
   잔고와 mismatch 가능 (예: trader 죽은 동안 stop 체결됨 → state 는 'open'
   인데 actual flat). 후속 PR 에서 startup reconciler 필요.
5. **Daily loss limit kill**: -200 USDT 초과 시 차단. 그러나 차단 후 다음 날
   KST 자정 reset 자동. 운영자 확인 없이 reset 되는 위험 → 후속 PR 에서
   manual unlock 도입 검토.

## 활성화 전 체크리스트

- [ ] BinanceFuturesBroker 구현 + ccxt or 자체 client
- [ ] Startup reconciler (broker 실 잔고 vs SQLite state)
- [ ] Daily loss kill 후 manual unlock
- [ ] docker-compose service 추가
- [ ] 첫 dry-run 24h 운영 → daemon FIRE 알림 대비 trader 가 100% 매칭 처리
      확인 (즉, top-100 fire 중 KST gate 통과한 것만 정확히 'placed' 또는
      audit-able 'skipped')
- [ ] paper 6개월 후 PF 0.9+ 유지 → 실거래 ramp

## 관련

- [[live-airborne-bb-reversal-kst-hours]] — PR #327 (TOP30 한정 orchestrator strategy)
- [[live-airborne-bb-reversal]] — v0 (rejected)
- [[live-airborne-bb-reversal-v11]] — v1.1
- [[38-airborne-indicator-reverse-engineering]] — 시그널 수식
- [[39-airborne-manual-trading-checklist]] — 수동 매매 가이드

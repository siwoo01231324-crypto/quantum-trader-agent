---
type: runbook
id: airborne-trader-activation
name: Airborne Trader 실거래 활성화 — 단계별 체크리스트
severity: high
created: 2026-05-27
updated: 2026-05-28
owner: siwoo
status: ready
tags:
- airborne
- standalone-trader
- runbook
- activation
---

# Airborne Trader 실거래 활성화 가이드

[[airborne-trader-daemon]] skeleton (#329) + Binance broker / reconciler /
kill switch / docker-compose (#follow-up PR) 머지 후 실거래 활성화 절차.

⚠️ **본 가이드 모든 단계 완료 전엔 dry_run 유지**. 한 단계라도 skip 하면
자본 손실 위험.

## 0. 사전 조건 (PR 들 머지 확인)

- [ ] PR #327 머지 — `live-airborne-bb-reversal-kst-hours` (orchestrator, TOP30)
- [ ] PR #329 머지 — `airborne_trader_daemon` skeleton
- [ ] 본 PR 머지 — `BinanceFuturesBroker` + reconciler + kill switch + docker-compose

확인:
```bash
git log --oneline -10 origin/master | grep -E "airborne|trader"
```

## 1. Binance API key 발급 (선물 권한)

1. Binance → API Management → Create API
2. **Restrictions**:
   - ✅ Enable Futures
   - ❌ Disable Withdrawals (안전)
   - ❌ Disable Trading on Spot (Futures 만)
   - ✅ Restrict access to trusted IPs only (사용자 PC 의 외부 IP)
3. `BINANCE_API_KEY` + `BINANCE_API_SECRET` 안전한 곳에 저장

## 2. .env 설정

`.env` 파일에 추가:
```env
BINANCE_API_KEY=<발급한 API key>
BINANCE_API_SECRET=<발급한 secret>

# Airborne Trader 전용 설정
AIRBORNE_TRADER_DRY_RUN=true            # 첫 1주는 반드시 true
AIRBORNE_TRADER_POSITION_USD=200
AIRBORNE_TRADER_MAX_POSITIONS=10
AIRBORNE_TRADER_DAILY_LOSS_USD=-200
AIRBORNE_TRADER_DAEMON=qta-airborne-daemon
AIRBORNE_TRADER_STATE_PATH=/data/state.db
```

## 3. 컨테이너 빌드

```bash
docker compose -f docker-compose.live.yml build airborne-trader
```

마지막 줄에 `Successfully tagged qta-phase2:latest` 확인.

## 4. dry-run 시작 — 첫 1주

```bash
docker compose -f docker-compose.live.yml up -d airborne-trader
docker compose -f docker-compose.live.yml logs -f airborne-trader
```

기대 로그:
```
[airborne_trader] starting — dry_run=True position_usd=200 max_concurrent=10 kst_hours=[8, 11, 16, 22]
[airborne_trader] reconciler — no open positions
```

매 30초마다 polling. KST 8/11/16/22시 daemon FIRE 발생 시:
```
[airborne_trader] DRY_RUN PLACE BTCUSDT BUY qty=0.001234 @ 65432.10 (fire.close)
```

다른 시각 fire 는 SKIP:
```
[airborne_trader] SKIP BTCUSDT long @ ... — kst_hour=4 not in [8, 11, 16, 22]
```

### 4a. 매일 확인

```bash
docker compose -f docker-compose.live.yml exec airborne-trader python /app/scripts/airborne_trader_daemon.py --status
```

출력 예시:
```
== AirborneTrader Status (2026-XX-XXT...) ==
  state_path: /data/state.db
  dry_run: True
  kill_switch_active: False
  today realized PnL (KST 자정~): +0.00 USDT (limit -200)
  open positions: 0 (max 10)
```

### 4b. 1주 dry-run 후 검증

- daemon FIRE 알림 수 (Telegram) vs trader 의 "PLACE" 로그 수 — KST {8,11,16,22}시 발화한 것만 PLACE 떠야 정상.
- KST 다른 시각 fire 는 모두 SKIP 으로 audit 되어야 (`docker logs ... | grep SKIP`).
- mismatch 발견 시 활성화 *금지* — issue 보고 후 디버그.

## 5. 실거래 활성화

### 5a. dry-run off + 컨테이너 재시작

`.env` 수정:
```env
AIRBORNE_TRADER_DRY_RUN=false
```

```bash
docker compose -f docker-compose.live.yml up -d --force-recreate airborne-trader
docker compose -f docker-compose.live.yml logs -f airborne-trader
```

기대 로그:
```
[airborne_trader] starting — dry_run=False ...
```

### 5b. 첫 발주 실시간 모니터링

- Binance 앱/웹 으로 첫 entry order 도착 즉시 확인
- order quantity 가 `position_usd / fire_close` 맞는지
- client order id 가 `airb-` prefix 인지

## 6. 일상 운영

### 일일 PnL 점검 (KST 23:50 직전)

```bash
docker compose -f docker-compose.live.yml exec airborne-trader python /app/scripts/airborne_trader_daemon.py --status
```

`today realized PnL` 확인. -200 USDT 근처 도달하면:
- kill switch 자동 trigger
- 모든 신규 진입 차단
- 보유 포지션은 stop/TP 정상 청산

### Kill switch 풀기 (사용자 판단 후)

```bash
docker compose -f docker-compose.live.yml exec airborne-trader python /app/scripts/airborne_trader_daemon.py --unlock-daily-kill
```

출력:
```
kill switch 해제: ok=True  triggered=...  reason=...
```

### 데이터 검사 (SQLite WAL)

```bash
sqlite3 ./logs/airborne_trader/state.db "SELECT * FROM positions WHERE status='open';"
sqlite3 ./logs/airborne_trader/state.db "SELECT * FROM kill_switch ORDER BY id DESC LIMIT 5;"
sqlite3 ./logs/airborne_trader/state.db "SELECT date(exit_ts), SUM(realized_pnl_usd) FROM positions WHERE status LIKE 'closed_%' GROUP BY date(exit_ts) ORDER BY 1 DESC LIMIT 10;"
```

## 7. 중단 / 일시 정지

### 컨테이너 정지 (보유 포지션 유지)

```bash
docker compose -f docker-compose.live.yml stop airborne-trader
```

→ Binance 측 stop/TP order 가 없으므로 *수동으로 stop loss 가 안 잡힘*. 본
trader 는 polling-based monitor 라 process 죽으면 stop/TP 미체결.

**중요**: 컨테이너 정지 전에 반드시 broker UI 에서 보유 포지션 *수동* close
하거나, broker stop-market order 미리 걸어둘 것. 후속 PR 에서 broker 측에도
stop/TP order 미리 거는 메커니즘 검토.

### 완전 비활성화 (재진입까지 막기)

```bash
docker compose -f docker-compose.live.yml down airborne-trader
# .env 에서 AIRBORNE_TRADER_DRY_RUN=true 로 복귀
```

## 8. 비상 시

### kill switch 가 active 인데 풀고 싶지 않을 때

= 영구 차단 의도. `.env` 에서 `AIRBORNE_TRADER_DAILY_LOSS_USD=99999` 같이
음수 값을 *불가능한 양수* 로 둬도 active 차단 유지 (state 의 kill_switch
row 가 우선).

### 모든 보유 포지션 즉시 청산

본 trader 자체엔 mass-close CLI 없음. 후속 PR 에서 추가 검토.
임시:
```bash
# Binance UI 에서 직접 close → SQLite state 는 다음 trader 재시작 시 reconciler 가 자동 정리.
docker compose -f docker-compose.live.yml restart airborne-trader
```

## 후속 PR 작업 (활성화 후 검토)

1. `mass-close` CLI 명령
2. Broker 측 stop/TP order 자동 placement (polling-based stop 의 risk 해결)
3. Telegram alert (kill switch trigger, 첫 발주 시점)
4. Web UI / dashboard 통합 — open positions, today PnL 가시화

## 관련

- [[airborne-trader-daemon]] — architecture spec
- [[live-airborne-bb-reversal-kst-hours]] — orchestrator strategy
- [[39-airborne-manual-trading-checklist]] — 수동 매매 보조

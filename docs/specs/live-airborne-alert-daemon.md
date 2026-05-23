---
type: spec-architecture
id: live-airborne-alert-daemon
name: Live Airborne v1.1 USDT-perp alert daemon (Binance Futures + Telegram)
owner: siwoo
status: accepted
tags:
- airborne
- alerts
- live
- binance
- telegram
---

# Live Airborne v1.1 Alert Daemon

`scripts/airborne_alert_daemon.py` — Binance USDM Futures **top-N USDT-perp** 유니버스를 실시간 감시하며 Airborne BB-reversal v1.1 신호가 봉 확정될 때 Telegram 알림을 발송한다.

> **이 데몬은 자동 매매가 아니다.** [[airborne-family-overview]] 의 가족 전체 `status: rejected`. 5y multi-regime backtest 에서 PF<1, 알파 ≈ 0. 알림은 *시각 가이드 / 재현 카논* 용이며, 어떤 자동 매매 의존도 금지 (CLAUDE.md "주문 실행 LLM 위임 금지" 와 별개로 정책적 제약).

## 신호 정의 (v1.1)

[[live-airborne-bb-reversal-v11]] 의 close-based 돌파 + 40% 되돌림. Pine 보존본 `docs/specs/strategies/live-airborne-bb-reversal.pine` (TV slot `USER;d9f4857aaf05421ab3817870c8e99934`). 트리거 수식:

```
# Long (BB 하단 돌파 → 40% 되돌림 후 close 회복)
lower_thr   = bb_lower * (1 - min_close_margin)         # default margin = 0.001
breakout    = close[i] < lower_thr[i]
            AND close[i-1] >= lower_thr[i-1]
            AND |close[i] - open[i]| / open[i] >= min_body_pct   # default = 0.005
base        = close[i]
extreme     = min(low[j])   for j >= i (running)
trigger     = extreme + 0.4 * (base - extreme)
fire        = barstate.isconfirmed AND close >= trigger

# Short = mirror (BB 상단 돌파 + 40% pullback)
```

코드: `src/signals/airborne_bb_reversal.py::evaluate_long_fire_v11` / `evaluate_short_fire_v11` (16 unit tests, hermetic).

## 데이터 흐름

```
┌──────────────────────────────────────────────────────────────┐
│ Startup                                                       │
│   1. fetch_futures_24h_snapshot()  ← fapi /ticker/24hr        │
│   2. top_n_by_volume(snapshot, n)  ← USDT-quote 필터          │
│      stablecoin/leverage 토큰 자동 제외                       │
│   3. bootstrap_history(symbols, intervals=(1h,5m))            │
│      ← /fapi/v1/klines limit=100(1h), 50(5m) per symbol       │
│      → states[symbol].history_1h / history_5m                 │
├──────────────────────────────────────────────────────────────┤
│ Steady state                                                  │
│   BinanceMarketDataStream(symbols, ["1h","5m"], !markPrice…)  │
│     wss://fstream.binance.com/stream?streams=...              │
│   ▼                                                           │
│   for ev in stream:                                           │
│     ├─ KlineEvent(interval="5m", is_closed=True)              │
│     │    → append to history_5m  (max 100 bars)               │
│     ├─ KlineEvent(interval="1h", is_closed=True)              │
│     │    → append to history_1h  (max 200 bars)               │
│     │    → evaluate_and_dispatch() ← Airborne v1.1 long+short │
│     │       └─ fires → cooldown check → notify()              │
│     └─ MarkPriceEvent  → consumed silently (MVP)              │
└──────────────────────────────────────────────────────────────┘
```

핵심 모듈:
- `src/universe/binance_futures_snapshot.py` — fapi 24h ticker → snapshot DataFrame
- `src/universe/binance_top.py::top_n_by_volume` — stablecoin/leverage 자동 제외
- `src/brokers/binance/market_ws.py` — 공개 마켓 WS (kline + markPrice) + REST kline bootstrap
- `src/signals/airborne_bb_reversal.py` — v1.1 helpers (long + short)
- `src/observability/alerts.py::notify` — Telegram dispatch (LIVE > QTA > legacy token chain)

## Telegram 페이로드

```
ℹ️ [INFO] Airborne v1.1 LONG — BTCUSDT (1h)
40% retrace fired at 55234.5 (trigger 55210.1, base 56100, extreme 54800)
• symbol: BTCUSDT
• timeframe: 1h
• side: long
• fire_close: 55234.5
• trigger: 55210.1
• base: 56100
• extreme: 54800
• 5m_preview: ascending          ← 최근 3×5m close 방향
• note: v1.1 reproduction — family rejected; visual guide only
```

## 운영

### 토큰 (이미 설정됨)
`.env` 에 `TELEGRAM_LIVE_BOT_TOKEN` / `TELEGRAM_LIVE_CHAT_ID` (LIVE > QTA > legacy fallback). `_resolve_telegram_env` 가 자동 라우팅 — 미설정 시 stdout 으로 graceful degrade.

### 실행
```bash
# 운영
python scripts/airborne_alert_daemon.py --top-n 50

# 드라이런 (stdout, no Telegram)
python scripts/airborne_alert_daemon.py --top-n 5 --dry-run

# 테스트넷
python scripts/airborne_alert_daemon.py --testnet --top-n 3 --dry-run

# Universe 재산출 주기 변경 (default 6h)
python scripts/airborne_alert_daemon.py --top-n 50 --universe-refresh-hours 12

# Legacy: universe 고정 (startup 1회만, 재산출 비활성)
python scripts/airborne_alert_daemon.py --top-n 50 --universe-refresh-hours 0
```

### Cooldown
같은 `(symbol, side)` 쌍은 4시간 (`COOLDOWN_HOURS=4`, 1h 봉 4개) 이내 재발화 억제. long/short 는 독립 (long fire 가 short cooldown 에 영향 X).

### 재시작
무상태 — 종료 시 마지막 fire 타임스탬프 휘발. 재시작 직후 동일 봉에서 다시 fire 할 수 있음 (cooldown reset). 의도된 단순화.

### Windows 자동 시작 (영구 운영)
ngrok 자동 시작과 동일한 패턴 — 부팅/로그온 시 자동 가동. 두 가지 fallback 패스 제공:

**1차 — Task Scheduler (RestartOnFailure 자동 재시작 포함, admin 필요할 수 있음)**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_airborne_alert_task.ps1
```
- LogonTrigger + ExecutionTimeLimit 0 (무제한 long-running)
- RestartOnFailure: 1분 간격 × 10회
- 배터리 모드에서도 가동 (laptop-safe)
- 작업 이름: `QuantumTrader_AirborneAlert`

**2차 — Startup 폴더 (admin 무관, 권장 fallback)**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_airborne_alert_startup.ps1
```
- `shell:startup` 에 `QuantumTrader_AirborneAlert.lnk` 생성 → 다음 로그온 자동 시작
- 등록 직후 detached minimized 로 즉시 가동 (재부팅 불요)
- 자동 재시작 없음 (`BinanceMarketDataStream` 의 내부 reconnect 로 WS 끊김은 자동 회복)

공통:
- 로그: `logs/airborne_daemon.log` (append, 수동 rotation)
- 실행 래퍼: `scripts/run_airborne_daemon.bat` (cd → python → log redirect)
- 데몬 인자는 .bat 파일의 `python -u ...` 줄 직접 수정 (재등록 불필요)

운영 상태 확인 / 정지 / 제거:
```powershell
# Task Scheduler 패스
Get-ScheduledTask -TaskName "QuantumTrader_AirborneAlert" | Get-ScheduledTaskInfo
Stop-ScheduledTask -TaskName "QuantumTrader_AirborneAlert"
Unregister-ScheduledTask -TaskName "QuantumTrader_AirborneAlert" -Confirm:$false

# Startup 폴더 패스
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where { $_.CommandLine -match 'airborne' }
Stop-Process -Id <PID>
Remove-Item "$([Environment]::GetFolderPath('Startup'))\QuantumTrader_AirborneAlert.lnk"
```

### Universe 새로고침 (2026-05-21 추가)
**Default 6시간 주기 자동 재산출.** `--universe-refresh-hours <h>` 로 조정, `0` 으로 비활성화 (legacy: startup 1회 → 무한 stream).

매 cycle:
1. `fetch_futures_24h_snapshot` → `top_n_by_volume` 로 새 universe 산출
2. `compute_universe_diff(prev, curr)` → `(added, removed, unchanged)`
3. removed 종목의 `SymbolState` 삭제 (cooldown 포함 — 다시 들어오면 리셋)
4. added 종목 REST kline bootstrap (1h×100 + 5m×50) → 새 `SymbolState`
5. unchanged 종목 history/cooldown 은 **유지** — fire 연속성 보장
6. 새 universe 로 `BinanceMarketDataStream` 재생성 → WS 재연결
7. `asyncio.wait_for(consume_task, timeout=refresh_secs)` 로 다음 cycle 까지 소비

**WS 재연결 영향**: cycle 경계에서 짧은 (~1초) 끊김. 다음 1h 봉 확정 시점 이전이라 신호 손실 없음. 5m bar 는 cycle 경계에 1~2개 누락 가능 — `_five_min_trend_preview` lookback 3 fallback 이라 무해.

**테스트**: `tests/scripts/test_airborne_alert_daemon.py` 의 6개 `compute_universe_diff` 단위 테스트 (added/removed/unchanged, 빈 prev, full replace, 순서 보존).

## 한계 / 면책

| 항목 | 상태 |
|---|---|
| 5y 알파 | **없음** (가족 전체 PF<1) |
| 자동 매매 적합성 | **부적합** — 알림 본문에 "rejected; visual guide only" 라인 박힘 |
| 신호 신뢰도 | 시각 재현 카논. 사용자 본인 손매매 판단 보조용 |
| markPrice 활용 | MVP 미사용 (kline 봉 확정만으로 평가). Phase 2 에 5m 청산 트레일링 경고 추가 예정 |
| 종목 추가/제거 (delisting) | 6h 주기 자동 재산출 (default) — 새 종목 자동 구독, 빠진 종목 정리. `--universe-refresh-hours 0` 으로 legacy startup-only 동작 가능 |

## 5y backtest 게이트 면제 사유

CLAUDE.md "새 전략 추가 시 5y backtest gate" 는 *새 전략* 에 적용. 본 데몬은 [[live-airborne-bb-reversal-v11]] 의 알림 wrapper 일 뿐 신규 전략 아님. v1.1 의 5y backtest 결과는 spec 에 이미 기재 (PF<0.82, all cost; rejected 등록). 알림 wrapper 는 알파를 *추가하지 않으며*, 신호 정의를 *변경하지도 않는다*. 따라서 신규 backtest 게이트 불필요.

## 테스트 (모두 통과)

| 파일 | 통과 | 설명 |
|------|---|---|
| `tests/signals/test_airborne_v11_helpers.py` | 16/16 | v1.1 long+short helper 단위 테스트 — hermetic OHLCV |
| `tests/universe/test_binance_futures_snapshot.py` | 6/6 | fapi 24h ticker 매핑 + http error 전파 (respx mock) |
| `tests/brokers/binance/test_market_ws.py` | 11/11 | kline/markPrice 파서 + URL 빌더 + REST kline (respx mock) |
| `tests/scripts/test_airborne_alert_daemon.py` | 8/8 | dispatcher + cooldown (long/short 독립) + 5m 트렌드 미리보기 + 바 append/eviction |

총 41 단위 테스트.

## 관련 노트

- [[airborne-family-overview]] — 가족 4 변형 비교 + 코드/Pine 위치 entry point
- [[live-airborne-bb-reversal-v11]] — v1.1 strategy spec (재현 카논)
- [[38-airborne-indicator-reverse-engineering]] — 역공학 사양
- [[live-universe-scanner-paradigm]] — 실시간 유니버스 스캔 패러다임

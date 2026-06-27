---
type: spec-architecture
id: airborne-daily-pnl-guards
name: 에어본 당일 손익 정지 게이트 3종 (이익목표·고점반납·손실한도)
status: in-progress
owner: siwoo
created: 2026-06-27
---

# 에어본 당일 손익 정지 게이트 3종

## 배경 (증상 — 2026-06-24~27 반복)

장중 초반(오전 KST)엔 +로 벌다가 오후·밤을 지나며 벌어둔 걸 다 토해내고
마이너스까지 가는 패턴이 24일부터 반복. 거래소 ledger 실측:

- **06-25**: 00-12 KST net **+20.01**(PF~2.5) → 12-24 KST **−23.76**(PF~0.25), 마감 −3.74
- **06-26**: 당일 고점 **+16.67**(19:39 KST) → 야간 배치에 ~5.6 반납, 마감 +11.05
- **06-27**(14시): 초반 +3.7 고점 후 줄곧 흘러내려 마감 −5.01 (롱 1승8패가 본체)

→ "오전 벌고 오후·밤 토해냄"을 막는 당일 손익 기반 자동 정지가 필요.

## 설계

`src/live/airborne_fire_consumer.py` 의 진입 단일 관문(`sweep_once` 시작부)에
**전략 무관 전체 정지** 게이트를 둔다. bb-reversal 롱 + short-whitelist 숏
양쪽이 같은 consumer 를 통과하므로 한 지점에서 당일 거래를 멈춘다.

3종(평가 순서 = 손실한도 → 이익목표 → 고점반납), 전부 **% of equity** 기준:

1. **이익목표(profit lock)** — `daily_pnl ≥ +PROFIT_TARGET_PCT%` → 정지.
   오른 날 익절 잠금(06-26 류).
2. **고점반납(give-back lock)** — 당일 intraday 고점이익이 `ARM_PCT%` 도달 후,
   그 고점의 `GIVEBACK_PCT%` 를 반납하면 정지. "벌었던 거 토해냄"에 정확히 대응.
   고점이 낮은 날도 잡음(06-27 류 일부).
3. **손실한도(loss lock)** — `daily_pnl ≤ -LOSS_LIMIT_PCT%` → 정지.
   초반부터 흘러내리는 날(06-27 류) 방어.

### 동작 규칙
- **신규 진입만 차단**. 미청산 포지션은 기존 TP/SL 그대로 진행(강제청산 안 함).
- **KST 자정 리셋**. daily PnL 은 `PnLAggregator.daily`(KST business-date 리셋)
  주입. intraday 고점도 KST date 로 키잉 → 날 바뀌면 리셋. **다음날 자동 재개**
  (별도 unlock 불필요 — 기존 `--unlock-daily-kill` 같은 수동 해제 없음).
- **정지 시 텔레그램 1회/일 통지** ("🛑 당일 거래 정지 — <사유>").
- **fail-open**: `daily_pnl_provider` 미주입 또는 `equity ≤ 0`(자본 미확보) 이면
  게이트 비활성(거래 허용) — 자본 미확보로 전량 보류되는 사고 방지.

### caveat (정확도)
native TP/SL·수동청산(숫자 broker coid)은 strategy_id 귀속 실패로 aggregator
`daily` 집계에서 누락될 수 있음(`pnl_aggregator.ingest_fill_event` 주석 참조).
→ 게이트가 실제 ledger 대비 약간 **늦게** 걸릴 수 있다. 후속 보강안:
주기적으로 거래소 `fetch_position_history_pnl` 로 in-process daily 를 정합.

## ENV 토글

| ENV | 기본 | 설명 |
|-----|------|------|
| `AIRBORNE_DAILY_GUARDS` | 0 | =1 이면 이익목표+고점반납 2종 ON (개별 토글로 덮어쓰기) |
| `AIRBORNE_DAILY_PROFIT_LOCK` | (매크로) | 이익목표 정지 on/off |
| `AIRBORNE_DAILY_GIVEBACK_LOCK` | (매크로) | 고점반납 락 on/off |
| `AIRBORNE_DAILY_LOSS_LOCK` | 0 | 손실한도 on/off — **매크로 제외, 명시 opt-in 만**(2026-06-27 사용자 결정) |
| `AIRBORNE_DAILY_PROFIT_TARGET_PCT` | 3.5 | 이익목표 % of equity |
| `AIRBORNE_DAILY_GIVEBACK_PCT` | 40 | 고점이익 반납 비율 % |
| `AIRBORNE_DAILY_GIVEBACK_ARM_PCT` | 1.0 | 고점이 이 %(of equity) 도달해야 락 무장 |
| `AIRBORNE_DAILY_LOSS_LIMIT_PCT` | 3.0 | 손실한도 % of equity (양수로 입력) |

**기본 전부 OFF** — 머지만으로 라이브 동작이 바뀌지 않게(검증 후 명시 활성).
켜는 법: `live_run.py` 환경에 `AIRBORNE_DAILY_GUARDS=1` (또는 개별 토글) 추가.

## 리스크 연동

- 기존 6종 진입 콘텐츠 필터(`AIRBORNE_FILTER_*`)와 독립 — 그 필터들은
  fire 단위 품질, 이 게이트는 당일 손익 단위 전체 정지. 직교.
- `airborne_trader/risk.py` 의 `daily_loss_limit_usd`(Binance testnet 좀비 경로)와
  별개 — 이 게이트가 진짜 Bitget 라이브 경로(host `live_run.py` → fire consumer).

## 구현·테스트

- 코드: `src/live/airborne_fire_consumer.py`
  (`_evaluate_daily_halt` / `_maybe_notify_halt` / `sweep_once` 훅).
- 배선: `src/live/loop.py::_start_airborne_fire_consumer`
  (`daily_pnl_provider = lambda: pnl_aggregator.daily`),
  `scripts/live_run.py` (`config.pnl_aggregator = pnl_aggregator`).
- 테스트: `tests/live/test_airborne_fire_consumer_daily_guard.py` (13건 —
  3종 발동/미발동 경계, 고점반납 arm·KST 리셋, fail-open, 매크로, sweep 단락).

## 참조

- [[airborne-fire-driven-consume]] — 발화 직접구동 consumer 본체

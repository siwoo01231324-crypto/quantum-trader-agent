---
type: spec-architecture
id: telegram-notifications
name: Telegram Notifications
title: Telegram Notifications — 운영 알림 + 라이브 청산 통지 (#227 S7)
status: adopted
owner: siwoo
created: 2026-05-11
last_updated: 2026-05-11
tags:
- telegram
- notifications
- ops
---

# Telegram Notifications

운영자에게 실시간 통지가 필요한 이벤트를 단일 텔레그램 채널로 fan-out 하는 정책 + 코드 위치.

## 발송 트리거 (WAL event_type 기준)

| event_type | 트리거 시점 | 메시지 형식 | 추가 시점 |
|---|---|---|---|
| `mode_switched` | broker 모드 전환 (KIS → Paper fallback 등) | ⚠️ + JSON snippet | #133 |
| `fill_anomaly` | 체결 이상 감지 (KIS vs sim 괴리) | ⚠️ + JSON snippet | #133 |
| `order_rejected` (KILL_SWITCH only) | kill switch 발동으로 주문 거부 | 🛑 reason | #133 |
| **`position_stop_triggered`** | **#227 LivePositionRiskManager stop/TP/trailing 발동** | **🛑/🎯/📉 한글 친화 메시지** | **#227 S7** |

기타 이벤트 (`order_acked`, `order_filled`, `signal_emitted`, `tracking_sample`, `run_started`, `session_open`) 는 빈도 높음 → 텔레그램 비-critical → daily report markdown 으로 일괄 전달.

## position_stop_triggered 메시지 포맷

```
🛑 stop_loss live_rsi_oversold_volume_spike
매도: 005930 @ 76400 (매수가 80000, -4.50%)
```

| 트리거 | 아이콘 | 의미 |
|---|---|---|
| `stop_loss` | 🛑 | 손절 (entry × (1 - stop_loss_pct)) |
| `take_profit` | 🎯 | 익절 (entry × (1 + take_profit_pct)) |
| `trailing_stop` | 📉 | 추적손절 (high_water × (1 - trailing_stop_pct)) |

## 환경 변수 (fallback chain)

| 우선순위 | 토큰 / chat_id |
|---|---|
| 1순위 | `TELEGRAM_LIVE_BOT_TOKEN` / `TELEGRAM_LIVE_CHAT_ID` (운영 표준) |
| 2순위 | `TELEGRAM_QTA_BOT_TOKEN` / `TELEGRAM_QTA_CHAT_ID` (#133 초기) |
| 3순위 | `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (legacy fallback) |

토큰/chat_id 둘 중 하나라도 빠지면 warn + skip — daemon halt 안 함.

## 코드 위치

| 파일 | 역할 |
|---|---|
| `scripts/telegram_alert.py` | WAL polling daemon — `--watch DIR` 모드. critical event 감지 → sendMessage |
| `scripts/telegram_rebal.py` | universe-scan paper rebal cron 결과 통지 (#226) |
| `scripts/telegram_control.py` | `/today`, `/positions`, `/fills`, `/account` 양방향 명령 (#221) |

## #227 S7 추가 사항

`scripts/telegram_alert.py`:
- `CRITICAL_EVENT_TYPES` 에 `position_stop_triggered` 추가
- `_format_position_stop(event)` 헬퍼 — 친화 한글 + 트리거별 아이콘 (🛑/🎯/📉)
- `is_critical_event(event)` 가 `position_stop_triggered` 우선 분기

LivePositionRiskManager (`src/portfolio/live_position_risk.py`) 의 `_emit_stop_event` 가 발행하는 WAL event 가 telegram_alert daemon (혹은 dashboard timeline broker) 에 의해 자동 fan-out.

## 단위 테스트

`tests/scripts/test_telegram_alert.py`:
- `test_position_stop_triggered_is_critical` — stop_loss 발동 시 critical 인식 + 메시지 검증
- `test_position_stop_take_profit_uses_target_icon` — 🎯 아이콘 적용
- `test_position_stop_trailing_uses_pulldown_icon` — 📉 아이콘 적용
- `test_non_position_stop_signal_emitted_not_critical` — 진입 신호 (signal_emitted) 는 critical 아님 (false-positive 빈도 차단)

## 관련

- [[live-universe-scanner-paradigm]] — `position_stop_triggered` 발생 컨텍스트
- 이슈 #133 (Phase 2 운영 텔레그램), #221 (양방향 텔레그램 control), #227 (라이브 청산 알림)

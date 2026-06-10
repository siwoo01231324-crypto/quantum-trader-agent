---
type: spec
id: realtime-data-stability
title: 실시간 마켓데이터 수집·리스크 실행 안정화
status: in-progress
created: 2026-06-10
---

# 실시간 데이터 안정화 (WS / 가격흐름 / SL)

## 배경 (사고)
운영 2026-06-09/10 — Bitget WS 3종(public trade / mark-price / orders)이 **30~60초
마다 "no close frame received or sent"로 false-disconnect**. 그 결과:
- 봉처리 루프(producer/consumer) 정지 → 발화 와도 **거래 0**
- synthetic SL(LivePositionRiskManager)이 mark-price 못 받아 **장님** → STG −22%,
  HOME −16.5% 등 **−5% SL 미준수, 손실 방치**

근본: `websockets.connect(ping_interval=20)` 의 *프로토콜* ping 을 Bitget 이 pong
하지 않아 `ping_timeout` 마다 멀쩡한 연결을 라이브러리가 끊음. Bitget 은 *앱레벨*
텍스트 `"ping"`/`"pong"` heartbeat 를 요구.

## 설계 원칙
1. **안전(SL/TP)은 실시간 피드에 의존하지 않는다 — 거래소 서버측에 둔다.**
2. **데이터(가격/신호)는 피드가 끊겨도 흐름이 안 막히게 격리한다.**

## 단계 (위험순·효과순)

### Phase 1 — WS keepalive (✅ 2026-06-10)
- `src/live/ws_keepalive.py`: 앱레벨 `app_level_heartbeat`(25초 "ping") + `is_keepalive_frame`("pong" skip).
- 적용: `BitgetPublicFeed`, `BitgetMarkPriceFeed`(feed.py), `BitgetOrderWS`(async_ws.py)
  — 모두 `ping_interval=None` + heartbeat + pong skip.
- 효과: 30~60초 false-disconnect 제거 → 봉처리·mark-price 흐름 정상화.

### Phase 2 — 서버측 SL 등록 + 검증 (예정)
- 진입 체결 직후 거래소에 **실제 stop 주문 등록 + REST 로 존재 검증**(preset
  silent-drop 방어). 봇/WS 죽어도 거래소가 −5% 청산. synthetic 은 2차 백업.

### Phase 3 — last-value cache + staleness (예정)
- WS push → in-memory {symbol: (price, ts)} 캐시. 거래루프는 캐시 read(논블로킹).
- staleness-aware: 가격이 N초 초과 stale → 신규진입 보류 + REST 보강 + 알림.

### Phase 4 — all-mark-price 단일스트림 + REST fallback (예정)
- per-symbol 구독 난립 대신 전 종목 mark price 단일 스트림 → 열린 포지션 전부
  자동 커버(synthetic SL 사각지대 해소). REST 폴링 backstop.

### Phase 5 — 관측성 + halt gate (예정)
- 스트림별 msg/s, 심볼별 가격 나이, 재연결 횟수 메트릭. staleness 임계 초과 시
  신규진입 halt. 포지션 진실원천 = 주기적 REST 거래소 조회.

## 검증
- Phase 1: `tests/live/test_ws_keepalive.py` (12). feed/bitget 회귀 무.

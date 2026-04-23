---
id: broker-runbook
type: onboarding
title: 브로커 어댑터 운영 Runbook
name: 브로커 어댑터 운영 Runbook
---

# 브로커 어댑터 운영 Runbook

관련 노트: [[broker-adapter]], [[kill-switch-dr]]

## 1. KIS 토큰 일일 발급 초과

**증상**: `KISAuth.get_token()` 에서 `msg_cd=EGW00123` 또는 HTTP 429 수신. 일일 토큰 발급 횟수 초과.

**원인**: KIS는 동일 appkey로 하루 최대 약 100회 토큰 발급 허용. 프로세스 재시작 반복 또는 캐시 미사용 시 소진.

**절차**:
1. `~/.cache/qta/kis_token.json` (또는 `KIS_TOKEN_CACHE_PATH` env) 파일 존재 확인.
2. 파일이 없거나 만료 전인데 재발급 중이라면 캐시 로직 버그 → `auth.py` `_load_cached` 경로 점검.
3. 파일이 유효(만료 5분 이상 남음)하면 프로세스를 재시작하지 말고 캐시를 재사용.
4. 한도 초과 상태면 다음날 00:00 KST 이후까지 대기. 장중 재기동이 필요하면 KIS 고객센터에 한도 증설 요청.
5. 재발급 성공 후 `health_check()` → `HealthStatus.OK` 확인.

**예방**: `KIS_TOKEN_CACHE_PATH` 를 영속 경로로 설정. 컨테이너 재시작마다 볼륨 마운트 보장.

---

## 2. Binance testnet 잔고 리셋

**증상**: `get_balance()` 반환값이 0 USDT 또는 예상치 못한 초기화.

**원인**: Binance testnet 은 정기적으로 잔고를 리셋함 (공지 없이 발생).

**절차**:
1. `BinanceFuturesAdapter.get_balance()` 로 잔고 조회 확인.
2. 잔고 0 확인 시 `https://testnet.binancefuture.com` 포털 로그인 → "Futures Testnet" → "Reset" 버튼 클릭.
3. 기본 지급: 10,000 USDT. 추가 필요 시 "Claim" 반복.
4. `get_position_risk()` 로 잔여 포지션 없는지 확인 (리셋 후 포지션도 초기화됨).
5. 통합 테스트 재실행.

**예방**: testnet 잔고를 전략 로직의 전제로 두지 말 것. 통합 테스트는 잔고 0 케이스에 graceful 처리.

---

## 3. listenKey 분실 / WS 24h forced disconnect

**증상**: Binance WS가 끊기거나 `{"code": 1008}` 오류 수신. 체결 이벤트 누락 가능성.

**원인**: Binance User Data Stream `listenKey` 유효 시간 60분. 30분마다 keepalive 실패 또는 24시간 만료.

**절차**:
1. `BinanceUserDataStream` 이 자동 재접속(`_reconnect()`)을 시도함 — 로그 `"Reconnecting user data stream"` 확인.
2. 재접속 성공 시 `ReconnectReconciler.reconcile_open_orders()` 가 REST `GET /fapi/v1/openOrders` 로 미수신 체결 보완.
3. 재접속 실패 시 (`reconnect_attempts` 소진): kill switch 자동 trip 또는 수동 대응.
   - `adapter.health_check()` → `HealthStatus.DOWN` 이면 OrderRouter 가 kill switch를 자동 trip.
4. 수동 복구:
   ```python
   stream.close()
   stream = BinanceUserDataStream(client, on_fill=..., on_raw=...)
   stream.start()
   ```
5. 재접속 후 `get_open_orders()` 결과와 내부 포지션 상태 대조.

**예방**: `BinanceUserDataStream` 은 23시간 주기로 선제 재접속 (`_preemptive_reconnect`). 로그 레벨 INFO 이상 유지.

---

## 4. KIS WS 세션 41개 한도

**증상**: WS 구독 요청에 `rt_cd != "0"` 또는 세션 거부 응답. 신규 종목 체결통보 수신 불가.

**원인**: KIS WS는 하나의 appkey 당 종목 구독 최대 41개 + 체결통보(H0STCNI9) 1건 = 총 42 슬롯.

**절차**:
1. 현재 활성 구독 목록 조회: `KISWebSocket._subscriptions` 확인.
2. 불필요한 종목 구독 해제:
   ```python
   ws.unsubscribe("005930")  # 삼성전자 등 비활성 전략 종목
   ```
3. 체결통보(H0STCNI9) 슬롯은 절대 해제 금지 — 해제 시 모든 체결 이벤트 누락.
4. 구독 해제 후 신규 종목 재구독 및 수신 확인.
5. 구조적 해결이 필요하면 전략 수를 줄이거나 종목 multiplexing 방식 설계 (후행 이슈).

**예방**: 전략 기동 시 필요 종목 목록을 사전 계산하여 41개 이내임을 검증.

---

## 5. env 오타로 CANO 파싱 실패

**증상**: 기동 시 `ConfigurationError: HANTOO_CREDIT_NUMBER must match ^[0-9]{8}-[0-9]{2}$` 또는 유사 메시지.

**원인**: `.env` 파일의 `HANTOO_CREDIT_NUMBER` 값이 형식 `12345678-01` 을 따르지 않음.

**절차**:
1. `.env` 파일 열기:
   ```
   HANTOO_CREDIT_NUMBER=12345678-01   # 8자리-2자리 형식
   ```
2. 하이픈 포함 10자리 형식인지 확인. 공백·특수문자 없어야 함.
3. 수정 후 프로세스 재시작.
4. `load_broker_config()` 성공 시 `CANO="12345678"`, `ACNT_PRDT_CD="01"` 로 분리됨.
5. `KISAdapter.get_balance()` 로 연결 확인.

**예방**: `.env.example` 에 올바른 형식 명시. CI에서 `load_broker_config()` smoke test 실행.

---

## 6. kill switch trip → release 절차

**증상**: `KillSwitchTripped` 예외 발생 또는 `qta_kill_switch_state{reason="..."} == 1`. 신규 주문 전면 차단.

**원인**: 드로다운 한도 초과(`auto:dd`), 브로커 health DOWN(`auto:health_check`), 이상 감지(`auto:anomaly`), 운영자 수동 trip(`manual:cli`).

**절차**:
1. Grafana `execution.json` 대시보드에서 trip 원인 확인: `qta_kill_switch_state` 라벨 `reason`.
2. 포지션 스냅샷:
   ```python
   positions = adapter.get_positions()
   ```
3. 미체결 주문 목록 확인:
   ```python
   open_orders = client.get_open_orders()
   ```
4. 청산이 필요한 경우 `emergency_exit=True` 주문은 kill switch 통과 허용됨:
   ```python
   req = OrderRequest(..., emergency_exit=True)
   adapter.place_order(req)  # KillSwitch.allow_order(liquidation=True) → True
   ```
5. 원인 분석 완료 및 운영자 승인 후 release:
   ```python
   ks.release(operator="siwoo@example.com")
   ```
6. release 후 `health_check()` → OK 확인. 전략 재가동.

**예방**: `kill-switch-dr.md` 의 DR 프로세스 정기 훈련. 드로다운 한도는 전략별 백테스트 기반 설정.

---

## 7. paper → live 전환 체크리스트

**사전 조건**: 백테스트 검증 전략, KIS 실전 계좌 개설, Binance 실전 API key 발급 완료.

```
[ ] 1. 환경변수 분리
       HANTOO_FAKE_* (paper) → HANTOO_REAL_* (live) 로 전환
       BINANCE_API_KEY / BINANCE_API_SECRET 실전 key로 교체
       BINANCE_BASE_URL=https://fapi.binance.com (testnet URL 제거)

[ ] 2. 어댑터 paper 플래그 해제
       KISAdapter(paper=False)
       BinanceFuturesAdapter(paper=False)
       TR_ID: VTTC* → TTTC* 자동 전환 (tr_ids.py)

[ ] 3. 브로커 라우터 활성화
       BROKER_ROUTER_ENABLED=true 설정 확인

[ ] 4. kill switch 상태 확인
       ks.tripped == False 확인
       qta_kill_switch_state == 0 (Grafana)

[ ] 5. 포지션 모드 검증
       adapter.ensure_position_mode(hedge=False)  # 또는 hedge=True — 전략 설계 따름

[ ] 6. 심볼 필터 로드 확인
       SymbolFilters._ensure_loaded() — 실전 exchangeInfo 파싱 성공

[ ] 7. 최소 주문 smoke test
       1주 (KIS) / 최소 notional (Binance) 로 LIMIT 주문 발주 → cancel 확인

[ ] 8. 메트릭 수신 확인
       qta_orders_total{broker="binance_futures"} 또는 qta_orders_total{broker="kis"} 증가 확인

[ ] 9. 로그 시크릿 마스킹 확인
       로그에 api_key/secret/token 평문 노출 없음 (SecretMaskingFilter)

[ ] 10. CI cron 시간 조정
        KIS 통합 테스트: 평일 KST 09:30 (UTC 00:30) cron 설정
```

---

## 8. KIS 통합 테스트 실행 윈도우

- 실행 가능 시간: **평일 KST 09:00~15:30** (장중)
- 공휴일: 수동 확인 필요 (자동 판정은 후행 이슈)
- CI nightly 권장 시각: KST 09:30 (UTC 00:30, 월~금 cron)
- 주의: 장 외 시간 실행 시 체결 없음 → WS fill 수신 불가 → `xfail` 처리

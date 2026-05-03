# chore: KillSwitch threading.Lock → asyncio.Lock 전환 (Phase 3+ 멀티스레드 대비)

## 배경

#80 Phase 1 plan 의 기술 부채 TD-7. 현재 `src/ops/kill_switch.py:32` 의 `KillSwitch` 가 `threading.Lock` 사용. Phase 1 의 단일 스레드 asyncio loop 환경에서는 문제 없지만, Phase 3+ 멀티스레드 운영 시 deadlock 위험.

## 의존성

- 선행: #80 머지 (KillSwitch 가 Phase 1 코드 경로 전반에서 사용)
- 영향 모듈: `src/ops/kill_switch.py`, `src/ops/triggers.py`, 기존 호출자 전체

## 범위

옵션 A (권장): asyncio.Lock 래퍼 추가
- `AsyncKillSwitch` 신규 — `asyncio.Lock` 기반, async API
- 기존 `KillSwitch` 보존 (sync 호출자 호환성)
- 호출자 (Phase 1 `executor.py`, `paper_broker.py`) 가 어느 것을 쓸지 선택

옵션 B: KillSwitch 의 lock 자체를 옵션 (`use_async_lock: bool`) 으로 분기
- 단일 클래스 + 런타임 분기

## 완료 기준

- [ ] 결정한 옵션 (A 또는 B) 구현 + 단위 테스트
- [ ] Phase 3+ 멀티스레드 시나리오 (스레드 동시 trip) 회귀 테스트
- [ ] 기존 호출자 (#80 의 `executor.py`, `paper_broker.py`, `triggers.py`) 호환성 검증
- [ ] `src/ops/.ai.md` 갱신

## 주의사항

- **본 이슈는 #80 머지 후 진행** — Phase 1 정상 동작 확인 후 변경
- 기존 sync 인터페이스 보존 필수 (backward compat)
- KillSwitch 의 trip / release / assert_allow_order 시그니처 변경 금지

## 참고

- #80 plan: `docs/work/active/000080-paper-broker/01_plan.md` 기술 부채 TD-7
- `src/ops/kill_switch.py:32` 현재 `_lock: threading.Lock`

## 연결 이슈

- 선행: #80
- 관련: Phase 3 Live Pilot (멀티스레드 운영 시 활성화)

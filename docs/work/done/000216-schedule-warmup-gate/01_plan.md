---
type: work-plan
id: 01_plan
name: "#216 live_run paper KIS schedule + warmup gate 구현 플랜"
status: draft
---

# #216 구현 플랜 (초안)

> ⚠️ AC 체크리스트만 담긴 **초안**. `/plan` 으로 구체적 구현 단계·테스트·검증 절차를 채워 넣어야 함.

## AC 체크리스트
- [ ] **Bug 1 fix** — `--schedule=krx` 가 KRX 시간 외 startup 시 다음 영업일 09:00 까지 sleep, KRX 시간 진입 후 정상 흐름 시작
- [ ] **Bug 2 fix** — `snapshot_builder.warmup()` 호출이 schedule 게이트 안에 위치 (KRX 시간 외엔 호출 자체 skip 또는 deferred)
- [ ] **Bug 3 fix** — WAL 첫 record 로 `run_started` (run_id, broker, symbols, schedule, build_sha) 기록 + KRX 영업일 진입 시 `session_open` heartbeat
- [ ] (선택) **Bug 4 fix** — `_RATE_LIMIT_SLEEP` 0.5 → 1.0 또는 기존 `src/brokers/kis/rate_limiter.py` 호출 직렬화 적용
- [ ] **운영 검증** — 다음 KRX 영업일 9:00 컨테이너 재기동 시 WAL 에 `run_started` + 1개 이상 tick + (가능하면) 1개 이상 신호 평가 record 확인
- [ ] **회귀 테스트** — `live_run --schedule=krx` 게이트 단위 테스트, warmup 게이트 테스트, WAL `run_started` 기록 테스트

## 다음 단계
1. `/plan` 으로 구체적 구현 계획 (각 Bug 별 패치 위치/방식, 테스트 추가 위치, 검증 시나리오) 채우기
2. TDD Red→Green→Refactor 사이클 시작

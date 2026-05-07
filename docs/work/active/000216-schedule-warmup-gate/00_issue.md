---
type: work-done
id: 000216-schedule-warmup-gate-00-issue
name: "#216 live_run paper KIS warmup→WS→tick→WAL 흐름 미작동"
status: active
---

# bug: live_run paper KIS warmup→WS→tick→WAL 흐름 미작동 — --schedule=krx 미구현 + 마감 후 warmup 무한 retry (#133/#152 후속)

## 배경
#133 Phase 2 모의계좌 4주 운영을 시작했지만 **WAL 에 단 한 번도 trade event 가 기록된 적 없음**. 26시간 + 새 빌드 컨테이너 모두에서 `logs/shadow/{run_id}/` 디렉토리는 만들어지지만 내부에 `wal.jsonl` 파일이 0건.

#152 PR (#214) 의 텔레그램 라우팅 fix 와 별개로, **운영 본질 흐름이 startup 자체에서 막혀 있음**.

## 진단 (2026-05-07 시우님 요청 아키텍처 진단 결과)

### Bug 1 (CRITICAL) — `--schedule=krx` argparse 만 있고 코드 미구현

```bash
$ grep schedule scripts/live_run.py src/live/loop.py
scripts/live_run.py:321:    "--schedule", choices=["krx", "always"], default="always",
# ← 이게 전부. args.schedule 참조 0건.
```

`docker-compose.live.yml:39` 에서 `--schedule=krx` 를 넘기지만 **noop**. KRX 시간 외에도 항상 실행.

### Bug 2 (CRITICAL) — warmup 이 KRX 시간 게이트 외부

`src/live/loop.py:244` `await snapshot_builder.warmup()` 가 schedule 게이트 없이 무조건 실행. KRX 마감 후 startup 시:
1. KIS REST `inquire-time-itemchartprice` 분봉 polling (3종목 × 13페이지)
2. KIS paper rate-limit (실제로는 매우 빡빡) 으로 EGW00201 폭주
3. backoff retry 로 결국 받긴 하지만 매우 느림
4. WS feed.connect() 에 도달 못 함 (warmup 후에 위치)
5. tick 0 → strategy 평가 0 → 주문 0 → WAL 비어있음

### Bug 3 (HIGH) — WAL 에 startup heartbeat 미기록

운영 시작 시 WAL 에 `run_started` 같은 첫 record 가 없어서 외부 모니터링이 작동/실패 식별 불가.

### Bug 4 (MEDIUM) — KIS paper rate-limit sleep 과소

`src/brokers/kis/price_client.py:6` 의 `_RATE_LIMIT_SLEEP=0.5` (paper 2 req/sec 가정) 가 실제로는 부족. EGW00201 자주 발생.

## 사용자 관점 목표
- 운영 시작 후 **다음 KRX 영업일 09:00 자동 진입** → warmup → WS → tick → 신호 평가 → 주문 → 체결 → WAL 에 정상 기록
- 마감 후 startup 시에도 다음 영업일까지 안전하게 sleep
- WAL/Telegram 으로 운영 상태 외부 가시화

## 완료 기준
- [ ] **Bug 1 fix** — `--schedule=krx` 가 KRX 시간 외 startup 시 다음 영업일 09:00 까지 sleep, KRX 시간 진입 후 정상 흐름 시작
- [ ] **Bug 2 fix** — `snapshot_builder.warmup()` 호출이 schedule 게이트 안에 위치 (KRX 시간 외엔 호출 자체 skip 또는 deferred)
- [ ] **Bug 3 fix** — WAL 첫 record 로 `run_started` (run_id, broker, symbols, schedule, build_sha) 기록 + KRX 영업일 진입 시 `session_open` heartbeat
- [ ] (선택) **Bug 4 fix** — `_RATE_LIMIT_SLEEP` 0.5 → 1.0 또는 기존 `src/brokers/kis/rate_limiter.py` 호출 직렬화 적용
- [ ] **운영 검증** — 다음 KRX 영업일 9:00 컨테이너 재기동 시 WAL 에 `run_started` + 1개 이상 tick + (가능하면) 1개 이상 신호 평가 record 확인
- [ ] **회귀 테스트** — `live_run --schedule=krx` 게이트 단위 테스트, warmup 게이트 테스트, WAL `run_started` 기록 테스트

## 의존성
- #213 (KIS rate-limit backoff) — 이미 머지됨
- #214 (#152 텔레그램 + cron) — 머지 후 이 이슈에서 운영 검증 가능

## 진단 출처
- 26시간 운영 컨테이너 (`qta-live-daemon`, `qta-report-cron`, `qta-telegram-notifier`) WAL 디렉토리 빈 상태 확인
- 새 빌드 컨테이너 startup 후 `logs/shadow/20260507T084458Z/` 도 빈 상태
- live-daemon 로그에 `signal|order|fill|tick` 키워드 매칭 0개
- `grep schedule scripts/live_run.py src/live/loop.py` → argparse 정의 1줄, 사용 0회

## 후속
- 본 이슈 머지 후 컨테이너 재빌드 → 다음 영업일 9:00 진입 모니터링
- 90거래일 누적 후 #97 후속 B-2 트리거 (#152 lake monitor 진척도 추적)

---

## 작업 내역
- 2026-05-07: `/si 216` 워크트리 생성, 보드 Backlog→Ready 이동, assign 완료. db8c5fd master HEAD (#214 KIS 1분봉 cron + 텔레그램 LIVE 봇 라우팅 머지 후) 에서 분기.
- 2026-05-07: `/ralph` 자율 루프 실행. PRD 6 stories 작성, US-001~004 + US-006 TDD Red→Green→Refactor 완료. (US-005 `_RATE_LIMIT_SLEEP` 상향은 schedule 게이트만으로 마감 후 폭주 해결되어 별도 후속 이슈로 미룸.)
  - **US-001**: `src/universe/krx_calendar.py` 에 `next_session_open(now)` 추가. 평일 장중/마감 후, 토요일/일요일/holiday/연속휴일 11 케이스 통과.
  - **US-002**: `src/live/schedule.py` 신규 — `wait_until_session_open(schedule, now_fn=, sleep_fn=)` async 헬퍼. 'always'/'krx' 분기 + KRX 시간 외 sleep 계산. mock clock 7 케이스 통과.
  - **US-003**: `ShadowConfig.schedule: Literal["krx","always"]` 필드 추가. `run_shadow_loop` 진입 직후 (ProcessLock 보다 먼저) `wait_for_session_fn(config.schedule)` 호출. `scripts/live_run.py:_build_config` 가 `args.schedule` 을 ShadowConfig 에 전달. 5 통합 테스트 통과 (gate 먼저 호출 + lock 미획득 검증 포함).
  - **US-004**: `loop.py:emit_startup_events` 신규 — WAL 첫 record 로 `run_started` (run_id/broker/feed/symbols/schedule/wal_path) 기록 + schedule='krx' 일 때 `session_open` (date/kst_open) 추가. 4 단위 테스트 통과 (replay 검증 포함).
  - **US-006**: `src/live/.ai.md` + `src/universe/.ai.md` 갱신, 풀 회귀 176/176 통과, `check_invariants --strict` 187 노트 통과.
- 2026-05-07: Smoke evidence 확보 (단위 테스트 외 운영 흐름 검증).
  - **Smoke 1** (`--schedule=krx` 마감 후): `python scripts/live_run.py --symbols 005930 --broker kis-paper-shadow --schedule krx --duration 8s --no-browser --feed kis --dashboard-port 0` 실행 시 `live.schedule outside session, sleeping 51150s (~14.2h) until 2026-05-08T09:00:00+09:00 KST` 로그 출력 + `logs/` 디렉토리 자체 미생성 (ProcessLock + WAL 보다 먼저 게이트가 차단). Bug 1+2 fix 동작 확인.
  - **Smoke 2** (`--schedule=always` 즉시 진입): `python scripts/live_run.py --symbols 005930 --broker paper-only --schedule always --duration 6s --no-browser --feed mock --mock-bars 5 --dashboard-port 0` 실행 시 `logs/live/{run_id}/wal.jsonl` 첫 줄에 `{"event_type":"run_started", payload:{run_id,broker,feed,symbols,schedule,wal_path}}` 기록. Bug 3 fix 동작 확인.
  - 운영 e2e 검증 (Bug 4 + 텔레그램 도달) 은 시우님 머지 후 컨테이너 재빌드 + 다음 KRX 영업일 09:00 진입 시 가능.

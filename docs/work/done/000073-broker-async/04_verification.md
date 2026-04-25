---
id: 000073-broker-async-04-verification
type: work-done
name: Broker Async 최종 회귀 게이트 검증
title: Broker Async — 최종 회귀 게이트 검증
status: done
issue: "#73"
---

# Broker Async — 최종 회귀 게이트 검증 (04_verification)

## 회귀 게이트 (AND 6개 — 전부 PASS 필요)

| # | 항목 | 기준 | 상태 |
|---|------|------|------|
| G1 | sync 테스트 전체 green | `pytest tests/brokers/ -q` 327+ 통과 | PASS |
| G2 | async p95 ≤ sync p95 × 1.1 | Scenario 1 결과 | PASS (0.8ms ≤ 413.5ms) |
| G3 | async req/s ≥ sync × 3.0 | Scenario 1 결과 | PASS (1478 ≥ 377) |
| G4 | 커버리지 ≥ 85% | `pytest --cov=src/brokers` | PASS |
| G5 | invariants strict | `python scripts/check_invariants.py --strict` | PASS |
| G6 | AC3 6항목 PR 수동 승인 | PR 리뷰 체크리스트 | 수동 확인 필요 |

## AC3 6항목 체크리스트 (PR 게이트 — 수동 확인)

> `docs/specs/broker-adapter-async.md` AC3 정의 기준. PR 머지 전 리뷰어 수동 확인 필요.

- [ ] **AC3-1** 병존 기간 정의: sync/async 2 minor 릴리즈 병존, v0.3.0 이전까지
- [ ] **AC3-2** 호출부 전환 순서: portfolio → execution → ops → backtest
- [ ] **AC3-3** deprecation 타임라인: v0.3.0 `@deprecated`, v0.5.0 완전 제거
- [ ] **AC3-4** 롤백 절차: `broker=None` 복귀 또는 `BROKER_ADAPTER_MODE=sync` 분기
- [ ] **AC3-5** sync/async 공통 타입 캐스팅 불필요, mypy `--strict` 오용 자동 검출
- [ ] **AC3-6** CI 변경: OS matrix, WindowsSelectorEventLoopPolicy, asyncio_mode=auto, 신규 deps

## 벤치마크 실측 결과 (2026-04-25)

```
Scenario 1 (REST 처리량):
  Sync  (ThreadPool-50): 125.7 req/s  p95=375.9ms
  Async (TaskGroup-200): 1477.9 req/s p95=0.8ms
  비율: 11.8x (게이트: ≥3.0x) → PASS

Scenario 2 (Fill 무결성):
  fills_expected=200  received=200  loss=0  dup=0 → PASS

Scenario 3 (Reconnect):
  reconnect_wall=3.22s (게이트: ≤10s) → PASS
  fill_gap=0 → PASS

Scenario 4 (50-symbol 동시):
  wall=0.018s  errors=0 → PASS
```

## 실행 명령

```bash
# G1: sync 회귀
pytest tests/brokers/ -q

# G2/G3: 벤치
pytest tests/performance/broker_sync_baseline.py -v
pytest tests/performance/broker_async_bench.py -v

# G4: 커버리지
pytest tests/brokers/ --cov=src/brokers --cov-report=term-missing -q

# G5: invariants
python scripts/check_invariants.py --strict

# G6: PR 수동 체크리스트 확인
```

## 이슈 히스토리

| 날짜 | 이벤트 |
|------|--------|
| 2026-04-25 | 플랜 합의 (ralplan consensus APPROVE) |
| 2026-04-25 | C1~C8 구현 완료 |
| 2026-04-25 | 벤치마크 실측 (4 시나리오 전부 PASS) |
| 2026-04-25 | C9 완료: orchestrator broker 주입, CI matrix, tests/conftest.py, .ai.md 최신화 |
| 2026-04-25 | G1~G5 게이트 PASS 확인, G6 PR 수동 승인 대기 |

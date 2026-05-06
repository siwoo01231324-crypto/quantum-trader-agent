---
type: work-done
id: 00_issue
name: "#133 hotfix — daemon stability (env_file path + WS reconnect)"
status: active
---

# fix: #133 KIS daemon 안정성 핫픽스 — env_file 경로 + WS 자동 재연결

## 사용자 보고 (2026-05-05)

`docker ps -a` 결과:

```
qta-live-daemon       Exited (0) 21 hours ago   ← 메인 데몬 사망
qta-report-cron       Up 28 hours ✅
qta-telegram-notifier Up 28 hours ✅
```

`docker logs qta-live-daemon` 마지막:
```
2026-05-05 00:28:00 ERROR ConnectionClosedError: sent 1011 (internal error)
keepalive ping timeout; no close frame received
2026-05-05 00:28:00 INFO  Live loop ended.
```

→ **WS keepalive 한 번 끊김에 데몬 자동 종료** (재연결 부재). 4주 운영 24h 만에 사망.

`docker start qta-live-daemon` 시도:
```
Error: error while creating mount source path '/run/desktop/mnt/host/d/project/
quantum-trader-agent/.worktree/000133-phase2-operation/data/state'
```

`docker compose ... up -d` 시도:
```
env file D:\.env not found: ../../.env (워크트리 가정 경로)
```

→ **워크트리 의존 경로** 가 머지 후 메인 repo 에서 작동 안 함.

## 두 가지 근본 원인

1. **워크트리 의존 경로**: `docker-compose.live.yml` 의 `env_file: ../../.env` 가 워크트리 (`.worktree/000133-phase2-operation/`) 에서만 해석됨. 메인 repo (`D:\project\quantum-trader-agent`) 에서는 `D:\.env` 로 풀려서 못 찾음.

2. **WS 재연결 부재**: `src/live/loop.py::producer()` 가 `async for tick in feed:` 한 번 끊기면 그대로 종료. KIS WS 는 keepalive ping/pong 주기적으로 timeout 가능 — 4주 동안 한 번도 안 끊긴다는 가정이 비현실적.

## 완료 기준
- [x] docker-compose.live.yml `env_file: ../../.env` → `./.env` (3 entries)
- [x] docker-compose.live.yml 헤더 주석 갱신
- [x] src/live/loop.py producer 에 reconnect 루프 추가 (backoff 1s → 60s, max 100 attempts)
- [x] 단위 테스트 — FlakyFeed 로 disconnect → reconnect 시나리오 검증
- [x] 풀 회귀 무회귀
- [x] check_invariants 통과
- [x] `src/live/.ai.md` 갱신

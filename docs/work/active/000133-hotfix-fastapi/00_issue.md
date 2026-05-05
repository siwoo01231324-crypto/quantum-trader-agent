---
type: work-done
id: 00_issue
name: "#133 hotfix bundle — fastapi/uvicorn deps + WAL 경로 통일 + daily_check_kis"
status: active
---

# fix: #133 KIS daemon 운영 hotfix bundle (#202 후속)

## 사용자 보고 (2026-05-05, #202 머지 후)

PR #202 merge 후 docker compose up 했더니 qta-live-daemon 이 즉시 사망:

```
ModuleNotFoundError: No module named 'fastapi'
... (재시작 후) ...
ModuleNotFoundError: No module named 'uvicorn'
```

추가로 #198 dashboard 에서 KIS run 이 안 보이는 것도 확인됨:
- shadow_runs.py 의 `classify_exchange("005930")` → "kis" 정상
- 그런데 KIS daemon 은 `./data/logs/` 에 쓰고 #198 은 `logs/shadow/` 만 스캔
- 결과 = path 불일치로 KIS WAL 이 dashboard 에 표시 안됨

## 근본 원인 3가지

1. **fastapi 가 dev-only 였음**: `pyproject.toml` 의 `[project.optional-dependencies] dev` 그룹에 fastapi/starlette 가 들어있어서 컨테이너 `pip install .` 시 누락. live_run.py:551 가 부팅 시 항상 import.

2. **uvicorn 도 누락**: dev 그룹에도 없었음. `_start_dashboard` (live_run.py:464) 가 `import uvicorn` 호출.

3. **#198 path 미스매치**: docker-compose.live.yml 의 bind mount 가 `./data/logs:/data/logs`. 호스트 path 가 `data/logs/` 라 #198 의 default `logs/shadow/` 와 다름. KIS WAL 이 dashboard 에 자동 노출되지 않음.

## 수정

### 1. pyproject.toml — fastapi/starlette/uvicorn 을 main deps 로

```toml
dependencies = [
    ...
    "fastapi",       # ← optional-dependencies/dev → main
    "starlette",     # ← 동일
    "uvicorn",       # ← 신규 추가
]
```

운영 컨테이너에 dashboard 가 항상 필요한데 dev-only 였던 게 문제. 주석에 #125/#178/#198 + #202 후속 디버깅 메모.

### 2. docker-compose.live.yml — WAL bind mount 경로 통일

3 entries (`live-daemon write`, `report-cron read`, `telegram-notifier read`) 의 `./data/logs:/data/logs` → `./logs/shadow:/data/logs`. `./data/state` (broker 토큰 캐시 + 프로세스 락) 와 `./data/reports` (일일 마크다운) 는 별개 관심사라 그대로 유지.

→ KIS daemon 이 호스트 `./logs/shadow/{run_id}/wal.jsonl` 에 쓰게 됨. #143/#199 와 같은 부모 디렉토리. **#198 dashboard `/shadow_runs` 가 자동으로 KIS run 표시.**

### 3. daily_check_kis.ps1 신규 (메인 repo 루트)

KIS daemon 운영 점검 1회 명령:
- 컨테이너 상태 (qta-live-daemon / report-cron / telegram-notifier)
- 최근 daemon 로그 15줄
- 최신 daily report (있으면) — `data/reports/` + `logs/shadow/reports/` 둘 다 자동 탐색
- WAL 이벤트 카운트 — `data/logs/` + `logs/shadow/` 둘 다 자동 탐색

하루 1회 더블클릭 또는 `.\daily_check_kis.ps1`.

## 검증

- [x] worktree 에서 docker compose down → build --no-cache → up
- [x] `docker run --rm qta-phase2:latest python -c "import fastapi, starlette, uvicorn"` 통과
- [x] `docker logs qta-live-daemon` 무에러:
  ```
  Dashboard listening at http://127.0.0.1:8000
  Loaded orchestrator from configs/orchestrator/production.yaml
  KIS token issued, expires_in=86400s
  SnapshotBuilder.warmup_loaded symbol=005930 bars=391
  SnapshotBuilder.warmup_loaded symbol=035720 bars=391
  SnapshotBuilder.warmup_loaded symbol=000660 bars=391
  ```
- [x] KIS REST 500 retry 작동 (장외 시간 정상)
- [ ] `/shadow_runs` 페이지에서 KIS run 카드 표시 확인 (사용자 PC, 머지 후)

## Refs #133 #202

\`Closes\` 미기재 — #133 본질은 4주 실측 운영. 본 PR 은 hotfix bundle.

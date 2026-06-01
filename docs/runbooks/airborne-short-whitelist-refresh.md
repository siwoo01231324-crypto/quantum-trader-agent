---
type: runbook
id: airborne-short-whitelist-refresh
name: Airborne SHORT-only Whitelist 주간 Refresh 절차
severity: P3
owner: siwoo
status: accepted
created: 2026-06-02
tags:
- runbook
- airborne
- whitelist
- weekly
---

# Airborne SHORT-only Whitelist 주간 Refresh

`live-airborne-short-whitelist-v1` 전략의 21종 whitelist 를 매주 토요일 새벽에 rolling 6개월 데이터로 재평가. 자동 commit 금지 — 사람 review 후 git commit.

## 권장 시점

| 항목 | 값 |
|---|---|
| 빈도 | **매주 토요일 KST 02:00** |
| 사유 | 한 주 데이터 마감 + 메이저 시장 휴장 시간 (잡음 적음) |
| 데이터 윈도우 | rolling **6개월** (현재 default) |

## 절차 (수동)

```powershell
cd D:\project\quantum-trader-agent

# 1. refresh 스크립트 실행 (yaml 직접 덮어쓰지 않고 .proposed 생성)
python scripts/refresh_airborne_short_whitelist.py

# 2. diff 확인
diff config/airborne_short_whitelist.yaml config/airborne_short_whitelist.yaml.proposed

# 3. review OK 면 덮어쓰기 + commit
Move-Item -Force config/airborne_short_whitelist.yaml.proposed config/airborne_short_whitelist.yaml
git diff config/airborne_short_whitelist.yaml
git add config/airborne_short_whitelist.yaml
git commit -m "chore: weekly airborne whitelist refresh $(Get-Date -Format yyyy-MM-dd)"
git push origin master
```

## 절차 (Windows Task Scheduler 자동화 — 옵션)

수동 review 강제하면서 자동 *생성* 만:

```powershell
# 매주 토요일 02:00 KST 자동 실행 (proposed 만 생성)
$action = New-ScheduledTaskAction -Execute "python.exe" `
    -Argument "scripts/refresh_airborne_short_whitelist.py" `
    -WorkingDirectory "D:\project\quantum-trader-agent"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 2:00am
Register-ScheduledTask -TaskName "QtaWhitelistRefresh" -Action $action -Trigger $trigger
```

이후 매주 토요일 아침 사용자가 dashboard 또는 git 에서 `.proposed` 파일 review → 합치기 → commit.

## State 머신 (지속성 규칙)

`scripts/refresh_airborne_short_whitelist.py` 가 적용하는 anti-churn 규칙:

| 전이 | 조건 |
|---|---|
| `candidate → active` | 3주 연속 rolling PF >= 1.0 + n_trades >= 30 |
| `active → warning` | 1주 rolling PF < 0.95 |
| `warning → removed` | 추가 1주 PF < 0.95 (2주 연속) **또는** 1주 PF < 0.85 |
| `warning → active` | 1주 PF >= 1.0 회복 |
| `removed → candidate` | PF >= 1.0 회복 (재진입 후보) |

## 변경 적용

yaml 갱신 후 **orchestrator 재시작 불필요** — `LiveAirborneShortWhitelistV1.get_universe()` 가 매 dispatch 마다 yaml 재로드. 다음 1h 봉 마감 시점부터 새 universe 반영.

## 모니터링

매주 refresh 후 확인 사항:

```powershell
# 변경 요약
python -X utf8 -c @"
import sys; sys.path.insert(0,'.'); sys.path.insert(0,'src')
from src.live.airborne_short_whitelist.whitelist_loader import (
    load_whitelist, active_symbols, candidate_symbols,
)
cfg = load_whitelist('config/airborne_short_whitelist.yaml')
print(f'as_of: {cfg.as_of}')
print(f'active ({len(active_symbols(cfg))}): {sorted(active_symbols(cfg))}')
print(f'candidate ({len(candidate_symbols(cfg))}): {sorted(candidate_symbols(cfg))}')
"@
```

## 알람 (TBD)

- whitelist 변경 시 Telegram 알림 — 별도 PR
- 신규 `active` 종목 = shadow mode 4주 (testnet 만 발주, mainnet 보류) — 운영 정책

## 외부 참조

- [[live-airborne-short-whitelist-v1]] — spec
- [[capital-allocation-v1]] — 3 전략 자본 배분
- `scripts/refresh_airborne_short_whitelist.py` — 실 스크립트
- `config/airborne_short_whitelist.yaml` — 현재 whitelist

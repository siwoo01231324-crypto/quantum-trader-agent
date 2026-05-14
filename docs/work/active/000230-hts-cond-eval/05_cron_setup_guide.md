# 옵션 B 운영 가이드 — Windows Task Scheduler 등록 (#230)

> 단타 검색식 universe 의 1분봉 + 일봉 데이터를 매일 KRX 마감 30분 후 적재. 5거래일 누적 후 옵션 A 백테스트를 1일 → 5일로 확장하여 채택 임계값 (trades ≥ 30) 도달.

## 사전 준비 확인

- [ ] `.env` 에 KIS 자격증명 4종 존재 (`HANTOO_FAKE_API_KEY`, `HANTOO_FAKE_SECRET_API_KEY`, `HANTOO_FAKE_CREDIT_NUMBER`)
- [ ] Python 가상환경에 `FinanceDataReader`, `pandas`, `pyarrow`, `python-dotenv`, `httpx`, `requests` 설치됨
- [ ] `D:\project\quantum-trader-agent\.worktree\000230-hts-cond-eval\` 워크트리에서 dry-run 정상 동작 확인:
  ```powershell
  cd D:\project\quantum-trader-agent\.worktree\000230-hts-cond-eval
  python scripts\cron_fetch_screener_universe.py --dry-run
  ```
- [ ] 절전·Docker 자동시작 설정 (memory: `project_qta_exe_run_from_repo_root.md`, `project_30day_daemon_hosting.md`)

## 등록 절차 (PowerShell, 관리자 권한 X)

### 1. python 경로 확인
```powershell
where.exe python
# 출력 예: C:\Users\watch\AppData\Local\Programs\Python\Python314\python.exe
```

### 2. schtasks 로 일일 작업 등록

평일(월~금) 16:00 KST 실행:
```powershell
$pythonExe = "C:\Users\watch\AppData\Local\Programs\Python\Python314\python.exe"
$worktree  = "D:\project\quantum-trader-agent\.worktree\000230-hts-cond-eval"
$script    = "$worktree\scripts\cron_fetch_screener_universe.py"

schtasks /Create /SC DAILY /TN "QTA-Screener-Fetch" /ST 16:00 `
  /D MON,TUE,WED,THU,FRI `
  /TR "cmd /c `"cd /d $worktree && `"$pythonExe`" -X utf8 `"$script`"`"" `
  /F
```

`/F` 는 동일 이름 작업이 이미 있을 때 덮어쓰기. 처음 실행은 `/F` 빼도 OK.

### 3. 등록 확인
```powershell
schtasks /Query /TN "QTA-Screener-Fetch" /V /FO LIST
```

### 4. 즉시 1회 실행 (수동 검증)
```powershell
schtasks /Run /TN "QTA-Screener-Fetch"
# 또는 직접:
cd D:\project\quantum-trader-agent\.worktree\000230-hts-cond-eval
python -X utf8 scripts\cron_fetch_screener_universe.py
```

### 5. 로그 확인
```
D:\project\quantum-trader-agent\logs\screener-fetch\screener_fetch_YYYY-MM-DD.log
```

성공 시 마지막 줄: `1m fetch done: ok=270~281 skipped=0 fail=0~10`

## 운영 로그 확인 체크리스트 (매일)

```powershell
# 가장 최근 로그 tail
Get-Content (Get-ChildItem D:\project\quantum-trader-agent\logs\screener-fetch -Filter "screener_fetch_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName -Tail 20
```

체크할 지표:
- `ok=` ≥ 95% of universe size (281 의 ~270 이상)
- `fail=` ≤ 5% (KIS rate limit 또는 일시적 오류)
- 실행 종료 시각이 시작 시각 + 20~40 분 (정상 페이스)

## 누적 진척도 모니터링

```powershell
# 누적 거래일 수 (lake 의 distinct date 카운트)
cd D:\project\quantum-trader-agent\.worktree\000230-hts-cond-eval
python -X utf8 -c "
import pandas as pd
from pathlib import Path
lake = Path('D:/project/quantum-trader-agent/lake/ohlcv/freq=1m/year=2026/month=05')
dates = set()
for sym_dir in lake.glob('symbol=*'):
    for p in sym_dir.glob('*.parquet'):
        df = pd.read_parquet(p, columns=['ts'])
        df['ts_kst'] = pd.to_datetime(df['ts']).dt.tz_convert('Asia/Seoul')
        dates |= set(df['ts_kst'].dt.date.unique().tolist())
print(f'Distinct trading dates in lake: {sorted(dates)}')
print(f'Total: {len(dates)} days')
"
```

5거래일 (5/15, 5/18, 5/19, 5/20, 5/21 또는 KRX 영업일 기준) 누적되면 본 검증 준비 완료.

## 본 검증 (5거래일 누적 후, 예상 2026-05-22)

⚠️ 현재 `run_hts_cond_pilot.py` 는 1일 walk-forward 만 지원. 5거래일 시계열로 확장하려면 `--multi-day` 옵션 추가 구현 필요 (별도 PR 또는 본 이슈 후속 단계).

확장 구현 후:
```powershell
python scripts\run_hts_cond_pilot.py --use-fdr --skip-fetch --multi-day 5
python scripts\grid_hts_cond.py --multi-day 5
```

채택 임계값: win_rate ≥ 50% AND avg_pnl ≥ +0.3% AND trades ≥ 30.

옵션 A 의 1일 grid 결과 (≤10:30 + DTS: 66.7% win, +0.586%, 15 trades) 가 5일 누적 시 ~75 trades 로 확장될 가능성 → trades ≥ 30 도달 시 ADOPT.

## 작업 해제 (실험 완료 후)

```powershell
schtasks /Delete /TN "QTA-Screener-Fetch" /F
```

## 트러블슈팅

| 증상 | 원인 후보 | 대응 |
|---|---|---|
| 로그에 KIS auth fail | .env 자격증명 누락/만료 | qta.exe 가 .env 읽는지 확인 (memory `qta-exe-run-from-repo-root`). HANTOO_FAKE_* 검증 |
| `ok=0 fail=281` | KIS API 다운 또는 IP 차단 | KIS Developers 포털 점검 / 자격증명 재발급 |
| 작업이 실행 안 됨 | Task Scheduler 비활성 / 사용자 미로그인 | "사용자 로그인 시" 옵션 + 자동로그인 설정 |
| 1m bars < 200 | 평일 비영업일 (휴장) 또는 fetch 중단 | 자동 skip 로직 작동 확인 (`is_krx_trading_day`) |
| 누적 dates 가 동일 날짜 덮어쓰기 | fetch_today_1m 가 partition 덮어씀 | (현재 설계 한계) 같은 날 여러 번 실행해도 마지막 결과만 보존 |

## 출처
- 옵션 A 결과: `03_pilot_report_2026-05-14.draft.md`
- Grid search: `scripts/grid_hts_cond.py` 출력 (≤10:30 + DTS = win 66.7%, +0.586%, 15 trades)
- 검색식 캡처 3장: 사용자 제공 (2026-05-14, 이슈 #230)
- KIS 분봉 API 당일 제한: #97 v5 검증
- 일일 cron 패턴 참조: `scripts/kis_1m_fetch_loop.sh` (#152)

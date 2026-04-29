# Phase 2 4주 운영 — Windows + Docker Desktop 셋업 가이드

Issue #133. 사용자 PC 24/7 ON 으로 daemon 운영. Oracle Cloud ARM 미가용 폴백.

## 사전 점검

| 항목 | 확인 |
|------|------|
| OS | Windows 10/11 |
| RAM | 8GB+ (4GB 는 Docker 에 할당) |
| 디스크 여유 | 50GB+ (Docker 이미지 + 4주 WAL + reports) |
| 인터넷 | 유선 권장 (KIS WS 연결 안정) |
| 정전 대비 | UPS (가능 시) — 4주 운영 중 정전 = WAL 무결성 위험 |

## 1. Docker Desktop 설치

1. https://www.docker.com/products/docker-desktop 다운로드
2. 설치 (관리자 권한). WSL2 backend 자동 활성화 (Windows 11 기본)
3. 설치 후 Docker Desktop 실행 → 우측 하단 트레이 아이콘 녹색 "Engine running" 확인
4. 검증: PowerShell 에서
   ```powershell
   docker version
   docker compose version
   ```

## 2. Docker Desktop 자원 할당

Docker Desktop → ⚙️ Settings:

- **General** → ✅ "Start Docker Desktop when you sign in" (PC 재부팅 후 자동 시작)
- **General** → ✅ "Open Docker Desktop dashboard at startup" (선택)
- **Resources** → Advanced:
  - **CPUs**: 4
  - **Memory**: **4 GB+** (live-daemon 1G + cron 256M + telegram 128M = ~1.4G + 호스트 OS 여유)
  - **Disk image size**: **50 GB+**
  - **Apply & Restart**

## 3. Windows 절전 / 재부팅 정책 (운영 안정성 핵심)

### 절전 모드 끄기

1. 제어판 → 전원 옵션 → 현재 plan 의 **"플랜 설정 변경"**
2. **모니터 끄기**: 사용자 편의 (예: 30분) — 모니터 꺼져도 daemon 돈다
3. **컴퓨터를 절전 모드로**: **사용 안 함** (필수)
4. **고급 전원 설정 변경**:
   - 하드 디스크 → 끄기: **사용 안 함**
   - 절전 → 다음 시간 후 최대 절전 모드: **사용 안 함**
   - USB 설정 → USB 선택적 절전: **사용 안 함**
5. 노트북이라면: **덮개 닫음 동작** = "아무 것도 안 함" (배터리/전원 둘 다)

### Windows Update 자동 재부팅 시간

`gpedit.msc` 또는 PowerShell 관리자:
```powershell
# Update 자동 재부팅을 KRX 외 시간 (새벽 03:00) 으로
$path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
New-Item -Path $path -Force | Out-Null
Set-ItemProperty -Path $path -Name "AUOptions" -Value 4
Set-ItemProperty -Path $path -Name "ScheduledInstallDay" -Value 1   # 매일
Set-ItemProperty -Path $path -Name "ScheduledInstallTime" -Value 3  # 새벽 3시
```
또는 GUI: 설정 → Windows Update → 고급 옵션 → "사용 시간" → 09:00-23:00 (이 외 시간만 재부팅 허용)

## 4. 첫 빌드 & 실행

```powershell
cd D:\project\quantum-trader-agent\.worktree\000133-phase2-operation

# 1. .env 등록 확인 (D:\project\quantum-trader-agent\.env)
#    필수: HANTOO_FAKE_API_KEY/SECRET/CREDIT_NUMBER, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
#    상세는 docs/work/active/000133-phase2-operation/SECRETS.md 참조

# 2. 이미지 빌드 (5~10분, 첫 실행만)
docker compose -f docker-compose.live.yml build

# 3. Telegram bot 작동 검증 (1회)
docker compose -f docker-compose.live.yml run --rm telegram-notifier python /app/scripts/telegram_alert.py --test
# → Telegram 으로 "✅ QTA Phase 2 telegram_alert test ping" 수신

# 4. 운영 시작 게이트 (다음 KRX 영업일 KST 10:00 이후, 한투 앱 거래 기록 확인 후만)
docker compose -f docker-compose.live.yml up -d

# 5. 첫 1시간 모니터링 (인증/주문/체결 path 정상 확인)
docker compose -f docker-compose.live.yml logs -f live-daemon

# 6. 상태 확인
docker compose -f docker-compose.live.yml ps          # 3 service healthy
docker stats                                          # 메모리 사용량
```

## 5. 일일 운영 루틴

| 시간 | 행동 | 자동/수동 |
|------|------|-----------|
| 09:00-15:30 KST | daemon 발주 (KRX 영업시간) | 자동 |
| 15:30 KST | KRX 마감 후 sleep | 자동 |
| 16:00 KST | `cron_loop.sh` 가 일일 리포트 생성 → Telegram 요약 | 자동 |
| 매일 1회 | Telegram 알림 확인 (mode_switched / kill_switch / fill_anomaly) | 수동 |
| 매주 금요일 | `02_operation.md` 주간 리뷰 + `data/reports/` git commit | 수동 |

## 6. 문제 발생 시

### Telegram 알림 미수신
```powershell
docker compose -f docker-compose.live.yml logs telegram-notifier --tail 50
```
→ "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정" 이면 `.env` 다시 확인.

### daemon halt (R1~R5 트리거)
```powershell
docker compose -f docker-compose.live.yml logs live-daemon --tail 100
type data\logs\<run-id>\wal.jsonl  | Select-Object -Last 20
```
→ Telegram 으로 알림 받음. 사후 분석 → `02_operation.md` 에 기록.

### daemon 멈춤 (Heartbeat 24시간 무응답)
PC 가 절전/재부팅 됐을 가능성. Windows 이벤트 뷰어 → 시스템 로그 확인. Docker Desktop 트레이 아이콘 확인.

### 긴급 중단
```powershell
docker compose -f docker-compose.live.yml down
```
graceful — WAL flush 후 종료. 재시작은 `up -d`.

## 7. 4주 종료

```powershell
# 1. daemon 정상 종료
docker compose -f docker-compose.live.yml down

# 2. 운영 일지 + ADR 작성
# docs/work/active/000133-phase2-operation/02_operation.md
# docs/work/active/000133-phase2-operation/03_adr.md

# 3. data/reports/*.md 일괄 git commit (사용자 review 후)
```

## 8. Phase 3 진입 시 — 클라우드 이전 권고

실자금 운영은 **로컬 PC 24/7 의 정전/재부팅 리스크에 노출 금지**.
- Oracle Cloud ARM 재시도 (capacity 풀릴 때까지 cron retry)
- 또는 AWS Lightsail / DigitalOcean ($4-5/월)
- ARM 채택 시 Dockerfile multi-arch 빌드 추가 필요 (`docker buildx build --platform linux/arm64,linux/amd64`)

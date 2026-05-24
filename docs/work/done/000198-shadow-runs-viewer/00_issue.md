---
type: work-done
id: 00_issue
name: "Issue #198 — 대시보드 Shadow Runs 뷰어"
status: active
---

# feat: 대시보드 "Shadow Runs" 뷰어 — Binance/KIS WAL read-only 통합 표시 (#143/#133/#199 후속)

## 사용자 관점 목표
qta.exe 대시보드 (localhost:3000) 에서 **모든 shadow 운영 결과를 한 페이지에서** 확인. 별도 cron 프로세스로 도는 봇들 (Binance R4 #143, Binance R6 #199, KIS #133, 추후 다른 변형 등) 의 누적 손익 / 거래 / 데몬 alive 상태를 production.yaml 5전략과 같은 대시보드에서 통합 추적.

## 배경
현재 두 종류 봇이 분리됨:
- **qta.exe 대시보드**: production.yaml 의 5전략 (orchestrator 내장) → 대시보드에 표시 ✅
- **Shadow daemon**: #143 (Binance r4-switch 4h, 가동 중) + #199 (Binance r6-switch 1h, 가동 중) + #133 (KIS, 미가동) → 별도 프로세스, 별도 WAL → **대시보드에서 안 보임** ❌

둘 다 가짜 거래인데 추적 채널이 분리돼서 사용자가 매일 PowerShell 명령 / wal.jsonl 직접 보거나 daily_check.ps1 돌려야 함. 통합 read-only 뷰어 1개로 해결.

**가짜 데이터 안 씀** — 빈 WAL 디렉토리도 graceful 처리 ("🟡 데몬 켜져있지만 신호 미발생"), 첫 신호 떨어지면 자동으로 실 데이터 표시.

## 완료 기준
- [ ] `GET /shadow_runs` — `logs/shadow/*` 디렉토리 목록 + 각 WAL 의 요약 통계
- [ ] `GET /shadow_runs/{run_id}` — 해당 run 의 상세 정보
- [ ] 거래소·종목 자동 분류 (BTCUSDT → Binance, 6자리숫자 → KIS)
- [ ] 봉 자동 분류 (`r4-switch` → 4h, `r6-switch` → 1h)
- [ ] 데몬 alive 임계값별 분류 (4h: 5h 미사용 → dead, 1h: 2h 미사용 → dead)
- [ ] HTML 페이지 (전략 카탈로그 옆 탭 또는 좌측 메뉴)
- [ ] 단위 테스트 + e2e 테스트 (실제 R4/R6 빈 WAL 로 graceful 동작 확인)
- [ ] `.ai.md` 갱신

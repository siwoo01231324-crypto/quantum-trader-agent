---
type: work-done
id: 00_issue
name: "#120 per_portfolio_risk 주기 평가기 watchdog + 알림"
status: active
---

## AC
- [ ] 주기 평가기 마지막 성공 timestamp 추적
- [ ] 30분 미실행 시 `qta_portfolio_risk_watchdog_state` 메트릭 + 텔레그램 알림 발동
- [ ] 사일런스 동안 신규 매수 자동 차단 (fail-closed)
- [ ] 단위 테스트: 평가기 실패 시뮬레이션 → watchdog 발동 + 신규 차단 확인

## 구현 위치
- `src/risk/watchdog.py` — PortfolioRiskWatchdog 클래스
- `src/observability/metrics.py` — qta_portfolio_risk_watchdog_state Gauge 추가
- `tests/test_portfolio_risk_watchdog.py` — 단위 테스트

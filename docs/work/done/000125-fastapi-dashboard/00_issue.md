---
type: work-done
id: 00_issue
name: "#125 FastAPI 로컬 대시보드 (4사분면 + Prometheus 메트릭 endpoint)"
status: active
---

## AC 체크리스트

- [ ] FastAPI 앱 (src/dashboard/app.py) + 정적 4사분면 UI
- [ ] /metrics Prometheus exposition format
- [ ] 손익 그래프 (실시간/일/월 토글)
- [ ] 6종 한도 사용률 게이지 (per_trade·per_day·per_portfolio·per_position·sector·drawdown)
- [ ] 신호 → 메타라벨러 → 주문 → 체결 타임라인
- [ ] 비상정지 4 트리거 상태 + 마지막 발동 시각 + 수동 발동/해제 버튼
- [ ] 통합 테스트: 모의 데이터 주입 후 UI 렌더링 확인

## 구현 계획

- `src/dashboard/app.py`: FastAPI 앱 (4사분면 HTML + /metrics)
- `src/dashboard/__init__.py`: 패키지
- `src/dashboard/.ai.md`: 디렉토리 메타
- `tests/test_dashboard.py`: TestClient 통합 테스트

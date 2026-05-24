# feat: DashboardState 라이브 PnL 와이어링 (전체·일간·전략별, KST 09:00 일일 리셋)

## 사용자 관점 목표
대시보드 Q1 의 실시간/일간/월간 PnL 이 KIS 모의계좌 실제 fill 이벤트로 실시간 갱신. 전략 카드/상세 페이지에도 "이 전략의 일간 수익" 표시.

## 배경
- 현재 `DashboardState.pnl_realtime`, `pnl_daily`, `pnl_monthly` 필드는 존재하지만 default 0.0 — KIS broker fill → state 갱신 와이어링 부재.
- 일일 리셋 기준 미정 → KRX 영업시간 시작인 **KST 09:00** 으로 고정.

## 완료 기준
- [ ] `PnLAggregator` (`src/live/pnl_aggregator.py`) — fill 이벤트 stream → 실시간/일간/월간 누적, 전략별 dict 도 같이 (`pnl_by_strategy: dict[str, float]`)
- [ ] KST 09:00 자동 일일 리셋 (timer + 부팅 시 last reset 복구)
- [ ] `scripts/live_run.py` 가 broker fill stream → aggregator → `DashboardState.pnl_*` 갱신
- [ ] `_enriched_catalog()` 가 각 카드에 `pnl_today` 추가 → HTML 카드에 "오늘 +1,234원" 표시
- [ ] 단위 테스트: 시뮬 fill 스트림 → 정확한 누적, KST 09:00 경계 리셋, 부팅 시 last reset 복구
- [ ] 통합 테스트: 모의 KIS fill → /api/pnl → 정확한 값

## 의존성
- 선행: #192 (strategy_id 태깅) — 전략별 PnL 분리에 필수 ✅ 머지됨 (2026-05-06, master `cb48831`)

## 비고
LLM 위임 금지 (CLAUDE.md 불변식 6) — PnL 계산은 결정론적 코드만.

## 작업 내역
- 2026-05-06: /si 194 — 워크트리 + 브랜치 생성, assign 완료. master `cb48831` (#192 머지 직후) 기반.

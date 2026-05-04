# feat: 매매 타임라인 실시간 스트리밍 (WebSocket) — 신호→메타라벨러→주문→체결

## 사용자 관점 목표
대시보드 좌하 4사분면의 "타임라인" 가 실시간으로 채워짐. 신호 발생 → 메타라벨러 통과/거부 → 주문 → 체결 4단계 이벤트 흐름.

## 완료 기준
- [x] `/ws/timeline` WebSocket endpoint — JSON 이벤트 스트림 (signal_emitted, metalabeler_decision, order_placed, fill_received)
- [x] WAL 의 기존 이벤트를 WS 로 fan-out (live + replay)
- [x] HTML 타임라인 UI — 최근 100건 무한스크롤
- [x] 백압 (back-pressure) 처리 — 클라이언트 느릴 때 drop 정책 (drop oldest)
- [x] 단위 테스트 (FastAPI TestClient WebSocket)

## 의존성
- 선행: PR #169 (#125 FastAPI) 머지
- 관련: WAL 구조 (#80 closed)

---

## 작업 내역
<!-- /remind-issue 와 작업 진행 시 여기에 누적 -->

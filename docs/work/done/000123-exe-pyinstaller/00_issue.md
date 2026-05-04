# feat: EXE 패키징 PoC (PyInstaller 단일 .exe 빌드 파이프라인)

## 배경
Phase 4 정식 운영의 핵심 — 사용자가 다운로드·더블클릭으로 시작하는 단일 EXE. 백서 §10-1.

## Phase / 월 10% 컨텍스트
- Phase 4 정식 운영 진입 필수 (외부 사용자 10명 KPI)
- 월 10% 목표와 직접 관련 없음 (전달 인프라)

## AC
- [ ] PyInstaller spec (asyncio + httpx + websockets + LightGBM 의존성)
- [ ] 단일 .exe 빌드 — 파일 크기 < 200MB 검증
- [ ] 최초 실행 지연 측정 (목표: 10초 이내)
- [ ] 윈도우 10/11 양쪽 부팅 확인
- [ ] 백신 오탐 발생 빈도 측정 (Defender·Avast·Norton)
- [ ] CI 파이프라인 통합 (PR 머지 시 자동 빌드)

## 의존성·참고
- 후행: DPAPI(#?)·대시보드(#?)·자동 업데이트(#?)
- 백서 §10-1 / 부록 B-3

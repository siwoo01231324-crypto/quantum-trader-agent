# feat: qta.exe 첫 실행 UX — 자동 브라우저 열기 + 콘솔창 유지

## 사용자 관점 목표
사용자가 qta.exe 더블클릭 → (1) 콘솔창 뜨고 일어나는 일 보임, (2) 5초 후 기본 브라우저로 http://localhost:3000 자동 열림, (3) 인자 누락 시 즉시 닫히지 않고 도움말 + \"press any key\" 대기.

## 배경
PoC 단계 EXE 는 더블클릭 시 인자 누락 → 즉시 종료 → 콘솔창 닫힘 → 사용자 \"뭐가 됐는지 모름\" 경험. 백서 §10-1 \"평범한 윈도우 사용자도 다운로드·더블클릭\" 목표 미달.

## 완료 기준
- [x] `scripts/live_run.py` 인자 없이 실행 시: 도움말 출력 + `input("Press Enter to exit...")` 대기
- [x] FastAPI 서버 시작 후 `webbrowser.open` 자동 호출 (옵션 `--no-browser` 로 끄기 가능)
- [x] 콘솔창에 시작 배너 (ASCII art "QTA" + 버전 + 등록된 전략 수)
- [x] Windows 단위 테스트 (subprocess.run + timeout)

## 의존성
- 선행: PR #169 (#125 FastAPI) + 본 production.yaml 이슈 머지

---

## 작업 내역
<!-- /remind-issue 와 작업 진행 시 여기에 누적 -->

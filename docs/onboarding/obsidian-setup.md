---
type: onboarding
id: obsidian-setup
name: "Obsidian 볼트 세팅 가이드"
---

# Obsidian 볼트 세팅 가이드

본 레포의 `docs/` 를 Obsidian 볼트로 열어 지식그래프·Dataview·온톨로지를 활용하기 위한 단계별 가이드.

## 1. Obsidian 설치
- 공식 다운로드: https://obsidian.md/
- macOS / Windows / Linux 지원. 라이선스는 개인 용도 무료.

## 2. 볼트 오픈
1. Obsidian 실행 → "Open folder as vault"
2. `quantum-trader-agent/docs` 디렉토리 선택
3. 좌측 사이드바에 `specs/`, `background/`, `ontology/`, `dashboards/` 등이 보이면 성공

## 3. 필수 커뮤니티 플러그인
Settings → Community plugins → Turn on community plugins → Browse 에서 설치:

| 플러그인 | 용도 |
|---------|------|
| Dataview | 프론트매터 집계 쿼리 (`docs/dashboards/*`) |
| Graph Analysis | 지식그래프 확장 분석 |
| Templater (선택) | 노트 템플릿 자동화 |

설치 후 Settings → Community plugins 에서 각 플러그인 enable.

## 4. 핵심 코어 플러그인 확인
- Backlinks, Outline, Graph view, Templates 를 켠 상태로 둔다 (레포의 `.obsidian/core-plugins.json` 에 사전 설정).

## 5. 그래프뷰 확인
- 좌측 리본의 그래프 아이콘 → Graph view 열기
- `momo-btc-v2`, `rsi-divergence`, `max-drawdown-5pct`, `BTCUSDT` 노드가 링크로 연결되어 보이면 정상
- 필터·태그·색상 설정은 `.obsidian/graph.json` 에 커밋됨

## 6. Dataview 대시보드 확인
- `docs/dashboards/strategies-live.md` 열기
- 리딩 뷰 모드 전환 (Ctrl+E / Cmd+E)
- 테이블 렌더링 성공하면 설정 완료

## 트러블슈팅
- 그래프가 비어 있으면: 프론트매터 `id` 와 파일명이 일치하는지 확인
- Dataview 렌더링 실패: 플러그인 enable 여부 확인, `Settings → Dataview → Enable JavaScript queries` 는 꺼도 무방
- 위키링크 깨짐: `docs/schemas/note-schemas.md` 의 id 규칙 참고

## 참조
- 프론트매터 규약: `docs/schemas/note-schemas.md`
- 온톨로지: `docs/ontology/.ai.md`

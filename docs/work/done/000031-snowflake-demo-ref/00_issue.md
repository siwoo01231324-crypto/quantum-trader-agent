---
type: work-done
id: 000031-snowflake-demo-ref-00-issue
name: "[research] Snowflake UGM 2026 대체데이터 트레이딩 데모 참고 자료 기록"
status: done
---

# [research] Snowflake UGM 2026 대체데이터 트레이딩 데모 참고 자료 기록

## 목적
Snowflake UGM 2026 박준호 발표(백화점 방문자수·카드소비 대체데이터 × 주식 백테스팅/LLM 인사이트 데모) 내용을 본 프로젝트 배경 자료로 상세 기록하고, 기존/예정 이슈들과의 연결점을 정리한다.

## 배경
발표 영상(https://snowflake.wistia.com/medias/80p2bxdtee)이 본 프로젝트와 다음 4개 영역에서 구체적 시사점을 준다:
- 대체데이터(방문자수·카드소비) 알파 팩터 후보
- Snowflake Marketplace 원클릭 데이터 연동 패턴
- Streamlit 기반 백테스팅·수익률 비교 UX
- 백테스팅 결과를 자연어 요약하는 LLM 투자 인사이트 레이어

영상은 자막이 없어 Whisper(small, ko)로 로컬 전사 후 요약.

## 완료 기준
- [ ] `docs/background/ref-snowflake-alt-data-trading-demo.md` 작성 (영상 핵심 10개 섹션: 배경·가설·파이프라인 5단계·데모·전략/결과·회고·해커톤 팁·본 프로젝트 적용 가능성·한계·출처)
- [ ] 본 프로젝트 이슈 #20(데이터 레이크)/#23(피처·알파)/#24(리스크 룰 DSL)/#30(LLM 에이전트)과의 매핑 표 포함
- [ ] 문서 하단에 원본 영상 URL + Snowflake 공식 문서 URL 출처 명시
- [ ] `docs/background/.ai.md` 최신화

## 구현 플랜
1. Wistia에 자막 없음 → Whisper로 로컬 전사
2. 트랜스크립트 기반 구조화 요약 작성
3. 본 프로젝트 적용 가능성 섹션을 이슈 매핑 표로 정리

## 개발 체크리스트
- [ ] 해당 디렉토리 .ai.md 최신화


## 작업 내역
- docs/background/ref-snowflake-alt-data-trading-demo.md 작성 (Whisper 전사 + 10섹션 요약)
- docs/background/.ai.md 에 항목 추가


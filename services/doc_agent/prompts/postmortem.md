# Postmortem Timeline Prompt

다음 인시던트에 대한 포스트모템 timeline 초안을 재구성해 달라.

- Incident: {{incident_id}}
- 발생: {{occurred}}
- 영향 전략: {{affected_strategies}}
- 위반 규칙: {{violated_rules}}

요구사항:
1. 발단 → 탐지 → 대응 시작 → 복구 완료 4단계 골격 유지.
2. 각 단계에 추정 시각과 trigger 이벤트를 bullet 로 배치.
3. 빈칸 유지 (실측값은 사람이 채워넣는다).
4. 한국어, Markdown bullet.

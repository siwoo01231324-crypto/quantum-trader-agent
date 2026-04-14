---
type: runbook
id: kill-switch-runbook
name: "Kill Switch & DR — 운영 런북"
severity: P2
---

# Kill Switch & DR — 운영 런북

> 사고 발생 시 본 문서를 그대로 따른다. 추측 금지.

## 1. 발동 (Kill)
### 1-1. 자동 발동 시
- Telegram/Slack 알림 수신 → 즉시 대시보드에서 `tripped=true` 확인.
- 이미 신규 주문은 차단됨. 다음 단계로 이동.

### 1-2. 수동 발동
```bash
python -m src.ops.cli kill --reason "<구체적 사유>" --operator <이름>
# 또는 Telegram bot: /kill <사유>
```

## 2. 청산 결정 (spec 6)
| 조건 | 명령 |
|---|---|
| 단일 포지션 < ADV 1%, 시장 정상 | 시장가 즉시 |
| 포지션 ≥ ADV 1% | TWAP 5~30분 분할 |
| 시장 이상 (서킷·갭·거래중지) | 청산 보류, 운영자 콜 |
| 브로커 API 다운 | 보류, 백업 채널(전화) 사용 |

청산 주문은 `KillSwitch.allow_order(liquidation=True)` 게이트를 통과한다.

## 3. 로그·증거 보존
1. `logs/incident_<ts>/` 폴더 생성
2. 다음을 복사:
   - 최근 30분 애플리케이션 로그
   - 브로커 API 요청/응답 덤프
   - 포지션·체결 스냅샷
   - `kill_state.json`
3. 즉시 백업 스토리지로 업로드.

## 4. 복구 체크리스트
- [ ] 트리거 원인 식별 (DD / API / 이상체결 / 수동)
- [ ] 재발 방지 패치 또는 파라미터 조정 PR
- [ ] Dry-run 모드에서 회귀 테스트 통과
- [ ] 페이퍼 트레이딩 1세션 무결함
- [ ] 운영자 2인 승인 (slack thread)
- [ ] `python -m src.ops.cli release --operator <이름>` 실행
- [ ] 사후 보고서 작성: `docs/work/done/incidents/<날짜>.md`

## 5. 절대 금지
- 원인 미식별 상태에서 release 금지
- 1인 단독 release 금지
- 로그 보존 전 시스템 재시작 금지

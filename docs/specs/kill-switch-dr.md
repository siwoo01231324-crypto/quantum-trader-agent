# Kill Switch & DR (Disaster Recovery) — 명세

## 1. 목적
자동매매 시스템에서 치명적 장애·이상 동작이 감지되면 **즉시 모든 신규 주문을 차단**하고, 보유 포지션을 안전하게 청산하며, 사후 복구까지 일관된 절차를 보장한다.

## 2. 범위
- 주문 게이트(전치 검증) 레이어
- 자동 트리거 (DD 한도, API 오류, 이상 체결)
- 수동 인터페이스 (CLI, Telegram, 대시보드)
- 청산 전략 결정 (시장가 vs TWAP)
- 복구 체크리스트

## 3. 아키텍처
```
[Strategy] → [Order Builder] → [KillSwitchGate] → [Broker API]
                                      ↑
                          [Triggers] [ManualSignal]
```
- 모든 신규 주문은 `KillSwitchGate.allow_order()` 를 통과해야 한다.
- 게이트가 `tripped` 상태이면 신규 주문 0건, 청산 주문은 별도 화이트리스트로 허용.

## 4. 자동 트리거 (3종)
| 트리거 | 임계값(기본) | 동작 |
|---|---|---|
| 일일 DrawDown | 자본 대비 -3% (config) | TRIP + Telegram 알림 |
| API 연속 오류 | 5회 연속 (config) | TRIP + 재연결 시도 |
| 이상 체결 패턴 | 1초 5건 동일 종목 (config) | TRIP + 전수 로그 덤프 |

## 5. 수동 인터페이스
- CLI: `python -m src.ops.cli kill --reason "manual"` / `release`
- Telegram bot: `/kill`, `/release` (별도 서비스에서 `KillSwitch.trip()` 호출)
- 대시보드 버튼: HTTP `POST /ops/kill` (게이트는 동일)

## 6. 청산 전략 결정 기준
| 상황 | 청산 방식 |
|---|---|
| 시장 정상 + 단일 포지션 < ADV 1% | 시장가 즉시 |
| 시장 정상 + 포지션 ≥ ADV 1% | TWAP 5~30분 |
| 시장 이상(서킷·갭) | 청산 보류, 알림만 |
| 브로커 API 다운 | 청산 보류 + 수동 콜 |

## 7. 복구 체크리스트
1. 로그·체결·상태 스냅샷 보존 (`logs/incident_<ts>/`)
2. 트리거 원인 분석 + 재발 방지 패치
3. Dry-run 모드 1회 통과
4. 페이퍼 트레이딩 1세션 정상 확인
5. 운영자 2인 승인 후 `release`
6. 사후 보고서 작성 (`docs/work/done/incidents/`)

## 8. Dry-run
- `KillSwitch(dry_run=True)` 모드에서는 `trip()` 호출 시 신규 주문은 차단하지만 **청산 명령을 실제로 보내지 않고 로그만 남긴다**.
- 테스트는 dry-run 경로만 사용한다.

## 9. 출처
- 본 명세는 본 레포 이슈 #27 의 AC 를 기반으로 작성됨.
- 관련 운영 사례: SEC Reg SCI(2014, https://www.sec.gov/rules/final/2014/34-73639.pdf), Knight Capital 사고(2012, https://www.sec.gov/litigation/admin/2013/34-70694.pdf).

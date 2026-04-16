---
type: spec-architecture
id: risk-rule-dsl
name: "Risk Rule DSL (Issue #24)"
owner: siwoo
status: draft
tags: []
---

# Risk Rule DSL (Issue #24)

## 1. 목적
매매 한도·손실 한도·포지션 한도를 **YAML 선언형 DSL**로 분리하여 전략 코드와 무관하게 긴급 한도 조정이 가능하도록 한다. 동일 정책 파일이 백테스트·페이퍼·라이브에서 동일하게 평가된다.

## 2. 설계 원칙
1. **선언적**: 룰은 YAML, 평가는 순수 함수 (사이드 이펙트 없음).
2. **합성 가능**: 여러 룰을 AND로 합성, 위반 시 첫 위반 룰을 반환.
3. **버전드**: `policy_version` 필드 강제, breaking change는 메이저 증가.
4. **검증 우선**: pydantic으로 파싱 단계에서 type/range 검증.
5. **중립적 평가**: `evaluate(policy, snapshot) -> Decision` 시그니처 고정.

## 3. YAML 스키마 (요약)

```yaml
policy_version: 1                # 정수, 필수
name: conservative               # 문자열, 필수
description: "보수적 한도"

per_trade:                       # 단일 주문 단위
  max_notional_krw: 5_000_000    # 단일 주문 최대 명목금액
  max_qty: 100                   # 단일 주문 최대 수량
  allowed_sides: [buy, sell]     # 허용 매매 방향

per_day:                         # 일일 집계
  max_orders: 50
  max_loss_krw: 500_000          # 일일 실현 손실 한도 (양수 입력)
  max_turnover_krw: 50_000_000   # 일일 거래대금 한도

per_portfolio:                   # 계좌 전체
  max_gross_exposure_krw: 100_000_000
  max_net_exposure_krw: 50_000_000
  max_leverage: 1.5              # gross / equity

per_position:                    # 종목 단위
  max_weight_pct: 10.0           # 단일 종목 비중 (%)
  max_qty: 1000

sector_limits:                   # 섹터 단위 (선택)
  - sector: tech
    max_weight_pct: 30.0
  - sector: finance
    max_weight_pct: 20.0

drawdown:                        # 누적 손익 기준
  max_intraday_dd_pct: 2.0       # 당일 고점 대비 -2%
  max_running_dd_pct: 8.0        # 운용 시작 고점 대비 -8%
  on_breach: halt                # halt | reduce | flatten
```

모든 한도는 **양수**로 입력하며 평가기에서 부호 처리.

## 4. JSON Schema (Draft 2020-12)
정형 검증을 위해 동일 구조를 JSON Schema로도 노출한다. (`src/risk/schema.json` 추후 자동 export.)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["policy_version", "name"],
  "properties": {
    "policy_version": {"type": "integer", "minimum": 1},
    "name": {"type": "string", "minLength": 1},
    "per_trade": {"type": "object"},
    "per_day": {"type": "object"},
    "per_portfolio": {"type": "object"},
    "per_position": {"type": "object"},
    "sector_limits": {"type": "array"},
    "drawdown": {"type": "object"}
  }
}
```

## 5. pydantic 모델 → 평가 흐름
```
YAML → yaml.safe_load → pydantic Policy.model_validate
                                  → Policy 객체
Snapshot(account, position, intent_order, daily_pnl, ...)
                                  ↓
                          evaluate(policy, snapshot) → Decision
                                  ↓
                ALLOW | BLOCK(rule_id, reason) | REDUCE(qty)
```

`Decision`은 `action ∈ {allow, block, reduce, halt, flatten}` + `rule_id` + `message`.

## 6. 정책 파일

`policies/conservative.yaml`, `policies/neutral.yaml`, `policies/aggressive.yaml` 3종 동봉. 운영자는 이 중 하나를 활성화하거나 fork하여 `custom.yaml` 작성.

## 7. 운영 규칙
- **편집은 PR 필수** — 정책은 코드와 동일하게 리뷰.
- **핫스왑 금지** — 라이브 중 정책 변경은 reload API로만 (감사 로그 남김).
- **breach 로그** — 모든 block/reduce/halt는 observability 채널로 forward.
- **테스트 필수** — 새 룰 추가 시 valid/invalid YAML + breach 케이스 단위 테스트.

## 8. 로드맵
- v1 (현재): 위 5개 카테고리 + drawdown.
- v2: 변동성 기반 동적 한도 (ATR×k 등).
- v3: 시간대별 한도 (장 마감 30분 전 ½).

## 9. 관련 노트

- [[19-portfolio-risk]] — v2·v3 에서 확장될 포트폴리오 레벨 CVaR·팩터 노출 이론 근거
- [[20-position-sizing]] — 사이징 결과가 본 정책의 `per_position.max_weight_pct` 에 clamp 됨
- [[12-validation-protocol]] — 백테스트 롤백 트리거가 본 정책의 평가와 연동
- [[kill-switch-dr]] — 정책의 `halt` 액션이 kill-switch 를 발동
- [[kill-switch-runbook]] — 위반 발생 시 운영 절차
- [[max-drawdown-5pct]] — 본 DSL 로 표현된 drawdown 룰 예시
- [[observability]] — `qta_risk_breach_total` 메트릭 송출 대상

## 10. 출처
- pydantic v2: https://docs.pydantic.dev/latest/
- JSON Schema Draft 2020-12: https://json-schema.org/draft/2020-12/release-notes
- PyYAML safe_load: https://pyyaml.org/wiki/PyYAMLDocumentation
- KRX 시장조성/리스크 가이드라인: https://open.krx.co.kr/contents/MMC/RULE/RULE/MMCRULERULE010.cmd
- BIS Margin & Risk Limits 개요: https://www.bis.org/publ/bcbs128.htm

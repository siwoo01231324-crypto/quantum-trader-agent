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

### 2.2 평가 순서 (first-violation-wins, #70)
`per_trade → per_day → per_portfolio → per_portfolio_risk → per_position → sector_limits → drawdown`

`per_portfolio_risk` 는 `Snapshot.portfolio_risk` 가 주입된 경우에만 동작 (주기 평가기 경로). 미주입 스냅샷에서는 no-op 이라 per-order 핫패스 비용 0.

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

per_portfolio:                   # 계좌 전체 (주문 흐름 제약)
  max_gross_exposure_krw: 100_000_000
  max_net_exposure_krw: 50_000_000
  max_leverage: 1.5              # gross / equity

per_portfolio_risk:              # 포트폴리오 상태 (주기 평가, #70 / #87)
  max_cvar_pct: 0.08             # Historical CVaR(α) 상한, positive loss fraction
  max_corr_avg: 0.80             # 평균 pairwise 상관 상한
  min_enb_ratio: 0.3             # ENB/N 하한 (19-portfolio-risk §7)
  alpha: 0.975                   # CVaR/VaR α (Basel III FRTB)
  # --- #87 특허 차용 확장 (모두 Optional, 기본값 None = 비활성) ---
  cvar_levels:                   # 다중 α CVaR 계층 (#87 P5). max_cvar_pct 이후 순차 평가, first-violation-wins
    - [0.95, warn]               # [alpha, label] — snap.cvar_levels[label].cvar_pct 가 max_cvar_pct 초과 시 on_cvar_breach 발동
    - [0.975, reduce]
    - [0.99, halt]
  extreme_fear_block: true       # 극단 공포 구간 신규 매수 차단 (#87 P4)
  extreme_fear_threshold: 0.2    # snap.fear_greed_proxy < threshold & intent.side=="buy" → BLOCK
  # stability_grade_min: D       # (out-of-scope for #87, follow-up: StabilityGrade DSL 배선)
  on_cvar_breach: reduce         # CVaR∝주문크기 → REDUCE
  on_corr_breach: block          # state → 신규만 BLOCK
  on_enb_breach: halt            # 구조 문제 → 사람 개입 필요

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
- **breach 로그** — 모든 block/reduce/halt는 observability 채널로 forward. `Decision.message` 는 `"<metric> <value> > <threshold>"` (혹은 `< threshold`) 포맷을 유지해야 감사 로그 파싱이 깨지지 않는다.
- **테스트 필수** — 새 룰 추가 시 valid/invalid YAML + breach 케이스 단위 테스트.

### 7.1 rule_id 라벨 공간 (Prometheus `qta_risk_breach_total{rule_id=...}`)
기존 + #70 포트폴리오 리스크 라벨 (출처: [[19-portfolio-risk]]):

- `per_trade.max_notional_krw`, `per_trade.max_qty`, `per_trade.allowed_sides`
- `per_day.max_orders`, `per_day.max_loss_krw`, `per_day.max_turnover_krw`
- `per_portfolio.max_gross_exposure_krw`, `per_portfolio.max_leverage`
- `per_portfolio_risk.max_cvar_pct` — Historical CVaR(α=0.975) 상한, 기본 action `REDUCE`
- `per_portfolio_risk.max_corr_avg` — 평균 pairwise ρ 상한, 기본 action `BLOCK`
- `per_portfolio_risk.min_enb_ratio` — ENB/N 하한 ([[19-portfolio-risk]] §7), 기본 action `HALT`
- `per_portfolio_risk.cvar_levels.{warn|reduce|halt|...}` — 다중 α 계층 CVaR (#87 P5), label 은 사용자 지정, action 은 `on_cvar_breach` 공유
- `per_portfolio_risk.extreme_fear_block` — `snap.fear_greed_proxy < threshold` 에서 buy 차단 (#87 P4), 기본 action `BLOCK`
- `per_position.max_qty`, `per_position.max_weight_pct`
- `sector_limits.<sector>`
- `drawdown.max_intraday_dd_pct`, `drawdown.max_running_dd_pct`

## 8. 로드맵
- v1: per_trade/day/portfolio/position + sector_limits + drawdown.
- **v2 (delivered #70)**: 포트폴리오 레벨 CVaR·평균 ρ·ENB — `per_portfolio_risk` 블록, `Snapshot.portfolio_risk` 주입, `src/risk/portfolio.py` 순수함수.
- **v2.1 (delivered #87)**: 특허 차용 확장 — `cvar_levels` 다중 α 계층 + `extreme_fear_block` (가격 기반 `fear_greed_proxy`) + `consensus_kelly`·`user_risk_vol_target` 사이저 옵션 + `equal_risk_contribution_convex`·`hrp_with_clustering` 포트폴리오 최적화 + `StabilityGrade` A~F 등급 (DSL 배선은 후속 이슈).
- v3: 변동성 기반 동적 한도 (ATR×k 등) · 시간대별 한도 · 팩터 노출 ([[19-portfolio-risk]] §5).
- **v3.1 (사용자 결정 의존, #119 권고)**: **Sleeve allocation 확장** — multi-PM 구조를 위해 정책 파일에 `sleeve_id` 필드 추가, sleeve 별 독립 `Policy` 인스턴스 (예: `core.yaml` / `satellite.yaml` / `experimental.yaml`), sleeve 별 kill switch 격리. orchestrator 는 `dict[sleeve_id, Policy]` 형태로 sleeve 별 평가 분기. sleeve 통합 리스크 (ENB·CVaR) 는 portfolio-level 로 측정 — sleeve 별 합산이 아님. [[19-portfolio-risk]] §6 v3 로드맵 참조. **선결 조건**: 사용자가 [[36-monthly-10pct-feasibility]] §7 에서 옵션 (d) Sleeve allocation 채택.

```yaml
# v3.1 예시 — sleeve 별 정책 (별도 파일 권장)
policy_version: 3
sleeve_id: aggressive_satellite  # 신규 v3.1
name: satellite_momo_vol_filtered
parent_policy: aggressive        # 선택, fallback 정책
allowed_strategies: [momo-vol-filtered]   # sleeve 격리: 다른 전략 거부
per_portfolio:
  max_leverage: 3.0              # sleeve 한정 L=3 허용
drawdown:
  max_running_dd_pct: 65.0       # sleeve B 한정 -65% 허용 (portfolio level 은 별도)
  on_breach: halt_sleeve         # sleeve 격리 halt — 다른 sleeve 영향 없음
```

## 8.1 #87 확장 상세 (모두 Optional, 기본값 None = 비활성 — 기존 정책 회귀 0)

| 필드 / 함수 | 위치 | 활성화 방법 | 테스트 파일 |
|---|---|---|---|
| `PerPortfolioRisk.cvar_levels` | `src/risk/dsl.py` | YAML `per_portfolio_risk.cvar_levels: [[α, label], ...]` | `tests/test_cvar_levels.py` |
| `PortfolioRiskReport.cvar_levels` | `src/risk/portfolio.py` | `historical_cvar_levels(returns, levels)` → `PortfolioRiskReport(..., cvar_levels=...)` | `tests/test_cvar_levels.py` |
| `Snapshot.fear_greed_proxy` | `src/risk/dsl.py` | orchestrator 가 `compute_fear_greed_proxy(price)` 호출해 `Snapshot(..., fear_greed_proxy=x)` 주입 | `tests/test_fear_greed_proxy.py` |
| `PerPortfolioRisk.extreme_fear_block` + `extreme_fear_threshold` | `src/risk/dsl.py` | YAML `per_portfolio_risk.extreme_fear_block: true` (+ optional threshold, 기본 0.2) | `tests/test_fear_greed_proxy.py` |
| `user_risk_vol_target(risk_score, vol_floor, vol_ceil)` | `src/risk/sizing.py` | 전략 구현에서 `vol_target()` 대신 호출 | `tests/test_sizing_user_risk.py` |
| `consensus_kelly(full_kelly, signal_agreement, k_base, k_max)` | `src/risk/sizing.py` | `MomoBtcV2(sizing_mode="half-kelly", use_consensus_kelly=True, signal_agreement=x)` | `tests/test_consensus_kelly.py` |
| `equal_risk_contribution_convex(cov, target_contrib)` | `src/risk/position_sizer.py` | 포트폴리오 사이저 경로에서 직접 호출 | `tests/test_position_sizer_erc.py` |
| `hrp_with_clustering(returns, k_clusters)` | `src/risk/position_sizer.py` | k_clusters=None 이면 단일 HRP fallback | `tests/test_position_sizer_hrp.py` |
| `StabilityGrade.grade(mcap, vol, dev)` | `src/universe/stability_grade.py` | pure function (DSL 배선 후속 이슈) | `tests/test_stability_grade.py` |

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

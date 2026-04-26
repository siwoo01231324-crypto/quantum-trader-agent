---
type: strategy
id: momo-btc-v2
name: BTC Momentum v2
status: backtest
instruments:
- BTCUSDT
timeframe: 15m
uses_signals:
- rsi-divergence
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-04-14
sharpe_bt: 0.1847
sharpe_live: null
tags:
- momentum
- crypto
---

# BTC Momentum v2

BTC 무기한 선물 15분봉 기준 모멘텀 전략. 진입 신호로 [[rsi-divergence]] 를 사용하고, 리스크 통제는 [[max-drawdown-5pct]] 규칙을 적용한다. 거래 대상은 [[BTCUSDT]] 한 종목.

## 진입
- [[rsi-divergence]] 가 bullish divergence 일 때 롱, bearish 일 때 숏.

## 진입 크기
- 기본 `sizing_mode="full"` (all-in, size=1.0) — 기존 거동 유지.
- 옵션: `sizing_mode="half-kelly"` — 최근 60 bar μ/σ 로 `kelly_continuous` → Half Kelly 적용.
- 옵션: `sizing_mode="vol-target"` — EWMA σ(λ=0.94) 로 연 20% 목표 사이징.
- 사이징 수학은 [[position-sizing]] 에 정의된 순수 함수 경유; LLM 개입 금지.

## 청산
- 반대 divergence 발생, 또는 [[max-drawdown-5pct]] halt 시. 매도 시 항상 전량(size=1.0).

## 훅 소비 (#81)

- `required_factors: ClassVar[list[str]] = ["rsi"]` — 엔진이 바마다 `context["factors"]["rsi"]` 에 Wilder RSI (period=14) 를 선계산하여 주입.
- `detect_divergence` 는 팩터가 아닌 **신호 해석기** (입력으로 RSI 시리즈를 받음). 레지스트리 미등록 유지, 전략 `on_bar` 내부에서 직접 호출.
- `compute_rsi` 직접 호출은 제거됨 (마이그레이션 #81). RSI 계산은 엔진 책임.

## 프로덕션 구성

### 이원화 활성화 (A/B 등록)

프로덕션에서는 두 strategy_id 를 **동시 활성화** 하여 온/오프 비교를 수행한다:

- **`momo-btc-v2`** — 메타라벨러 비활성화 (baseline)
- **`momo-btc-v2-meta`** — 메타라벨러 활성화 (LightGBM 임계값 0.5)

두 전략은 `configs/orchestrator/production.yaml` 에 각각 한 블록씩 등록되며, 같은 포트폴리오 내에서 병렬 실행된다. `MetaLabeler.load()` 는 자동으로 두 호출을 구분하여 처리한다 (#85 implementation.md 참조).

### 오케스트레이터 등록

`src/portfolio/orchestrator.py` 의 `AsyncStrategyOrchestrator` 는 두 ID 를 다음과 같이 등록한다:

```python
# 내부 자동 처리 (loader 경유)
orch.register_strategy(momo_btc_v2_off, "momo-btc-v2")
orch.register_strategy(momo_btc_v2_meta, "momo-btc-v2-meta")

# 수익률 시계열 공급 (리스크 모듈 입력)
orch.register_strategy_returns("momo-btc-v2", returns_off)
orch.register_strategy_returns("momo-btc-v2-meta", returns_meta)
```

두 호출 모두 필수. 생략 시 리스크 평가 경로가 **항상 ALLOW** → 리스크 관리 무력화 (see [[19-portfolio-risk]] §3.1).

### 모니터링 지표

Shadow Paper (#80) 는 매일 on/off 수익률을 기록한다. 다음 지표를 **rolling 7d 윈도우** 기준으로 추적:

| 지표 | 목표 | 악화 기준 |
|------|------|----------|
| **Sharpe (meta)** | baseline 대비 +0.20 이상 유지 | < baseline Sharpe − 0.10 |
| **MDD (peak-to-trough, promotion 이후)** | baseline 대비 ≤ -10%p | > baseline MDD + 5%p |
| **Trade Count Δ** | 필터 스킵 정상 | 전월 대비 ±50% 급변 시 조사 |

### 일일 수동 체크

```python
# 마일스톤 매일 (자동 배치로 로깅 가능)
quarantine_status = orchestrator.quarantined_strategies
# ⚠️ 둘 중 하나라도 quarantine 상태 → on-call 에 알림
```

## 롤백 런북

### 롤백 트리거

다음 중 **하나라도 발생** 하면 롤백 절차 시작:

1. **Sharpe 악화**: `momo-btc-v2-meta` 의 rolling 7d Sharpe < `momo-btc-v2` 의 rolling 7d Sharpe − 0.10
2. **MDD 악화**: `momo-btc-v2-meta` 의 peak-to-trough MDD > `momo-btc-v2` 의 MDD + 5%p (promotion 이후)
3. **Quarantine**: 두 전략 모두 또는 하나라도 `orchestrator.quarantined_strategies` 에 존재

### 롤백 절차

1. **일시 중단**: `configs/orchestrator/production.yaml` 에서 `momo-btc-v2-meta` 항목 **한 블록만** 주석 처리:
   ```yaml
   # - id: momo-btc-v2-meta
   #   ...
   ```

2. **확인**: 오케스트레이터 재시작 후 `orch.strategies` 에서 `momo-btc-v2-meta` 미등록 확인

3. **커밋**:
   ```
   revert(orchestrator): metalabeler off — Shadow Paper week-1 degrade
   trigger: Sharpe(meta=X.XX) < Sharpe(off=Y.YY) over 7d window
   artifact: <path-to-shadow-log>
   follow-up: #95 (자동 재학습) 후 재시도
   ```

4. **모니터링**: 이후 `momo-btc-v2` 단독 운영 5일 후 이슈 #95 (월별 재학습) 수동 트리거 검토

## 관련 노트

- [[13-feature-alpha-catalog]] — RSI 계산 로직·룩어헤드 방지 규칙
- [[12-validation-protocol]] — 본 전략의 백테스트 검증 (walk-forward, DSR/PBO)
- [[20-position-sizing]] — 진입 크기 이론적 근거 (Half Kelly + vol targeting)
- [[position-sizing]] — 사이저 구현 스펙 (`sizing_mode` 옵션)
- [[19-portfolio-risk]] — 멀티 전략 운영 시 상관·공분산 관리
- [[execution-algorithms]] — 주문 실행 (Market/Limit/TWAP)
- [[kill-switch-runbook]] — MDD halt 발생 시 청산 절차

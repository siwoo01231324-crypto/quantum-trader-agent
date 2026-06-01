---
type: spec-architecture
id: capital-allocation-v1
name: Capital Allocation & Leverage Plan v1 (3 전략 동시 운영)
owner: siwoo
status: accepted
created: 2026-06-02
last_updated: 2026-06-02
tags:
- capital-allocation
- leverage
- risk-management
- production
---

# Capital Allocation & Leverage Plan v1

3 개 전략을 동시 운영할 때의 자본 비중·레버리지·동시 보유 한도 정의. 2026-06-02 사용자 결정 (보수적 안 = 제안 1).

## TL;DR

| 전략 | 시드 비중 | per-trade size | 동시 한도 | leverage (코드) | venue |
|---|---|---|---|---|---|
| **cs-tsmom-crypto-daily** | **40%** | 1.33% × 30 종 = 40% | 30 (top_n) | **1x** | Binance Spot |
| **live-airborne-bb-reversal-kst-hours** | **20%** | 4% / trade | ~5 (KST 4시각 × 100종 분산) | **1x** (코드 default) | Binance Futures USDT-perp |
| **live-airborne-short-whitelist-v1** | **20%** | 4% / trade | ~5 (KST 19시각 × 15종) | **1x** (코드 default) | Binance Futures USDT-perp |
| **합계** | **80%** | | | | safety margin 20% |

## 시드 정의

| 환경 | 시드 | 비고 |
|---|---|---|
| testnet (현재 운영) | **3,297 USDT** | Binance Futures testnet 가짜 돈, 2026-06-02 시점 |
| mainnet | TBD | 4주 testnet 검증 통과 후 결정 |

## 전략별 상세

### 1. cs-tsmom-crypto-daily (메인 알파)

| 항목 | 값 |
|---|---|
| paradigm | universe-scan (cross-sectional) |
| venue | Binance Spot top-30 |
| timeframe | 1d |
| rebal_freq | 5d |
| 시드 비중 | **capital_fraction = 0.4** |
| per-pick size | 0.4 / 30 = **~1.33% / 코인** |
| top_n | 30 (코드 default) |
| min_picks | 3 |
| leverage | **1x** (Spot 현물) |
| stop / TP | BTC 252d drawdown ≤ -30% 시 전량 청산 (코드 default) |

**5y 백테스트**: Sharpe 1.33, 연수익 90.8%, MDD -52.4%. 본 운영의 *주력 알파*.

### 2. live-airborne-bb-reversal-kst-hours (보조)

| 항목 | 값 |
|---|---|
| paradigm | live-scanner (bidir) |
| venue | Binance Futures USDT-perp |
| timeframe | 1h |
| universe | binance_top_dynamic top-100 (24h volume) |
| 시드 비중 | **20%** |
| per-trade size | **default_size = 0.04** (4%) |
| 동시 한도 | 자연 분산 (KST {8,11,16,22} × 100종 × 평균 1~2h 보유 = ~5 동시) |
| leverage | **1x** (코드 default, 사용자가 Binance 웹에서 변경 가능) |
| stop / TP | 3% / 6% (R/R 1:2) |
| cooldown_after_stop_sec | 900 (15분) |

**5y 백테스트**: Sharpe 0.96, 연수익 46.3%, MDD -79.6%, PF 1.081.

### 3. live-airborne-short-whitelist-v1 (Hard OOS 검증 SHORT 알파)

| 항목 | 값 |
|---|---|
| paradigm | live-scanner (SHORT only) |
| venue | Binance Futures USDT-perp |
| timeframe | 1h |
| universe | `config/airborne_short_whitelist.yaml` 의 status=active 종목 (현재 15종) |
| 시드 비중 | **20%** |
| per-trade size | **default_size = 0.04** (4%) |
| 동시 한도 | 자연 분산 (KST 19시각 × 15종 = ~5 동시) |
| leverage | **1x** (코드 default, 사용자가 Binance 웹에서 변경 가능) |
| stop / TP | 3% / 6% (R/R 1:2) |
| retrace_ratio | 0.6 (Hard OOS 검증값) |
| atr_body_mult | 0.3 (Hard OOS 검증값) |
| cooldown_after_stop_sec | 900 |

**Hard OOS (2y test)**: test_PF=1.214, sumR=+1,395%, 5.45 trades/day, **1x leverage 가정**.

## Leverage 운영 원칙

### 코드 단 leverage = 1x 고정
- production.yaml 에 명시적 leverage 인자 없음 (live-scanner kwargs)
- 즉 코드는 항상 *명목가치 = position notional × 1* 발주
- Hard OOS 검증도 1x 가정

### 사용자 웹 컨트롤 (실 운영)
- Binance Futures 웹에서 종목당 leverage 변경 가능
- 변경 시 `default_size` 의 의미는 *margin 비중* 이 됨 (notional 은 leverage 배)
- 예: 4% margin × 10x = 40% notional 노출
- 같은 stop 3% / TP 6% 는 *가격 변동률* — leverage 무관 (단 ROI 는 leverage 배)

### 권장 leverage (testnet 검증 단계)

- **testnet 4주는 1x 권장** — Hard OOS 결과와 1:1 비교 가능
- 4주 후 PF·MDD 검증 통과 시 mainnet 으로 이전 + leverage 결정

## 동시 자본 충돌 방어

40 + 20 + 20 = **80%** → safety margin 20% 유지 (margin call / 거래소 일시 거부 대비).

### 충돌 시나리오
- 같은 종목 (예: LTCUSDT) 에 동시 매수 + 매도 시그널 발생 가능 (kst-hours 양방향 + short-whitelist SHORT)
- orchestrator 의 `min_order_interval_sec = 300` 가 동일 (sid, symbol, side) 중복 60초 차단
- per-strategy `cooldown_after_stop_sec = 900` 가 stop 후 동일 (sid, symbol) 재진입 15분 차단
- **방어 충분 — 진짜 청산 대비 자본 한도는 시드 80% 가 상한**

## Daily Loss Kill Switch

- AirborneTraderConfig 의 `daily_loss_limit_usd = -200 USDT` (기존)
- 단 본 spec 의 orchestrator-dispatched 전략은 별도 kill switch 없음
- **TBD**: orchestrator 레벨 daily kill switch 도입 검토 (별도 PR)

## 변경 절차

본 capital allocation 변경 시:
1. 본 spec 의 표 갱신
2. `configs/orchestrator/production.yaml` 의 kwargs 업데이트
3. patch-notes entry 추가
4. live_run.py 재시작 시 자동 반영

## 모니터링 (운영 중 확인 지표)

| 지표 | 권장 빈도 | 위치 |
|---|---|---|
| 시드 잔고 | 매일 | dashboard `/` 의 PnL venue card |
| 동시 보유 포지션 수 | 매일 | dashboard 전략별 포지션 카드 |
| 일일 PnL | 매일 | dashboard PnL today |
| Whitelist drift | 매주 토 02:00 | `scripts/refresh_airborne_short_whitelist.py` |

## 외부 참조

- [[live-airborne-bb-reversal-kst-hours]] — kst-hours spec
- [[live-airborne-short-whitelist-v1]] — short-whitelist spec
- [[cs-tsmom-crypto-daily]] — cs-tsmom spec
- `configs/orchestrator/production.yaml` — 실 운영 인자
- `docs/runbooks/airborne-short-whitelist-refresh.md` — 주간 whitelist refresh 절차

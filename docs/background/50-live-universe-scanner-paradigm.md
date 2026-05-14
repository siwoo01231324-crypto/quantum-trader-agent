---
type: research
id: 50-live-universe-scanner-paradigm
name: "Live Universe Scanner 알파 가설 + 5y 백테스트 검증 계획"
sources:
- internal:#227
- internal:cs_tsmom_kr_daily-bench-2026-05-06
- internal:momo_kis_v1-005930-paper-2026-04-15
---

# Live Universe Scanner 알파 가설 + 5y 백테스트 검증 계획

> 작성일: 2026-05-11 | 이슈 #227 | 별 spec: [[live-universe-scanner-paradigm]]

본 노트는 **검색식 + 자동매매 봇** 패러다임의 알파 가설을 학술 근거에 비추어 정리하고, 5년 백테스트 검증 계획을 명시한다. 본 패러다임은 #227 에서 도입된 **세 번째 strategy paradigm** (universe-scan / single-ticker 와 공존).

## 1. 알파 가설

### 1.1 임계값 진입의 알파 source

검색식 패러다임은 다음 세 가지 microstructure 효과를 가설로 한다:

1. **단기 평균회귀 (short-term reversal)** — 1~3일 단위에서 과매도 종목이 평균으로 회귀.
   - 학술: Lehmann (1990), Lo & MacKinlay (1990) — 1주일 시계열에서 횡단면 reversal 통계적 유의.
   - 본 패러다임 적용: `live_rsi_oversold_volume_spike`, `live_bb_lower_bounce`, `live_oversold_with_divergence`.

2. **돌파 모멘텀 (breakout momentum)** — 사전 저항선 돌파 후 수일~수주 추세 지속.
   - 학술: Donchian (1960), Faber (2007) Time-Series Momentum.
   - 본 패러다임 적용: `live_breakout_with_atr_stop`, `live_macd_bullish_cross_breakout`.

3. **거래량 점프 = 정보 신호** — 거래량 spike 가 정보 누적의 marker.
   - 학술: Llorente et al. (2002) — high-volume 일일 수익률의 자기상관 패턴이 정보거래 vs 유동성거래 구분.
   - 본 패러다임 적용: 모든 5종이 거래량/MACD/divergence 같은 secondary filter 사용.

### 1.2 universe-scan 패러다임과의 알파 교차

universe-scan 의 cross-sectional momentum 알파 ([[42-cross-sectional-momentum-crypto]], [[44-time-series-momentum-crypto]]) 는 **주간~월간** 시간축. live-scanner 는 **분~일** 시간축. 두 시간축의 알파는 부분적으로 독립 ([[universe-scan-strategy-pattern]] 의 5y bench Sharpe 0.871 이 살아있는 한 본 패러다임은 추가 알파 source).

다만 같은 종목을 두 패러다임이 동시 보유 시 위험 집중 가능성 — `risk.evaluate` 의 `per_symbol_concentration_limit` 가 합산 비중 게이팅으로 막음.

### 1.3 한국 시장 특수성

KRX 1분봉 universe (KOSPI 200 + KOSDAQ 150) 운영의 **장점**:

- 개인투자자 비중 높음 → 모멘텀/감정 효과 학술 근거 강함 (e.g. Lin & Chen 2007)
- 단방향 stop_loss / take_profit 는 한국 HTS 사용자에게 친숙 → 운영 직관성

**단점**:

- KRX 라운드트립 비용 ~55bp (commission + 슬리피지 + 거래세) → intraday turnover 높을 시 net negative 위험
- 종목별 거래정지 / 단일가 매매 / 사이드카 발동 시 broker 거부 → graceful 처리 필요
- 1분봉 OHLC 가 단일 가격 (close) 이라 ATR / volatility 계산 부정확 — KIS WS 시세 도입 후 해결 (#227 S5 follow-up)

## 2. 5년 백테스트 검증 계획

### 2.1 Universe 정의

| Universe | 정의 | Pin-date | n_symbols |
|---|---|---|---|
| KRX | KOSPI top-200 시총 + KOSDAQ top-150 거래대금 | 2021-05-11 | 350 |
| Binance USDT-Perp | 24h volume top-30 (stablecoin / wrapped 제외) | 2021-05-11 | 30 |

Universe pin-date 는 survivorship bias 인정 — `docs/specs/universe-scan-strategy-pattern.md` §"Survivorship bias" 참조. 정밀 PIT 검증은 후속 이슈.

### 2.2 검증 metric (per (strategy, universe))

| metric | 정의 | gate |
|---|---|---|
| Sharpe ratio | daily return mean / std × √252 | **≥ 0.5** (production 등록 조건) |
| MDD | max drawdown | ≥ -50% reject |
| AnnRet | (1 + cum_ret) ^ (252/N_days) - 1 | informational |
| Trades | total round-trip count | 1주당 < 1건 → 통계적 유의성 부족 reject |
| WinRate | (wins / trades) | informational |
| AvgHoldDays | mean(exit_day - entry_day) | < 0.5 day → 거래비용 폭증 위험 |
| RealizedPnL_profit / loss | sum(rets > 0) / sum(rets ≤ 0) | informational |

### 2.3 비용 모델

| Universe | round-trip cost | 출처 |
|---|---|---|
| KRX | 55 bp (commission 25bp + slippage 5bp + 거래세 25bp) | [[universe-scan-strategy-pattern]] §"비용 모델" |
| Binance | 10 bp (taker × 2) | [[42-cross-sectional-momentum-crypto]] |

intraday turnover 는 weekly rebal 보다 ~10배 → 비용도 ~10배. Sharpe gate 0.5 가 weekly 기준이므로 intraday 는 1.0 정도 권장 (별 가이드).

### 2.4 검증 실행

`scripts/bench_live_scanner.py` (#227 S6 skeleton) — 풀 5y 실행은 별 이슈 (#229 후속).

```bash
python scripts/bench_live_scanner.py --strategy live_rsi_oversold_volume_spike --universe krx --period 5y
python scripts/bench_live_scanner.py --all  # 5 strategy × 2 universe = 10 runs
```

각 run 의 결과는 해당 strategy spec frontmatter (`sharpe_bt`, `mdd_bt`, `annual_return_bt`, `trades_bt`) 에 기록.

### 2.5 통과 기준 + 운영 등록

| 결과 | 처분 |
|---|---|
| Sharpe ≥ 1.0 (intraday 권장) | `production.yaml` `enabled: true` 후보 — 사용자 결정 후 paper 모드로 1주 운영 검증 |
| Sharpe 0.5 ~ 1.0 | `production.yaml` 에 entry 유지 (commented), 추가 신호 필터 검토 |
| Sharpe < 0.5 | spec status `rejected`, production 미등록 |
| MDD < -50% 또는 trades < 5/year | reject (위험 또는 통계 유의성 부족) |

## 3. 운영 모니터링 (검증 통과 후)

대시보드 + 텔레그램 알림 (#227 S7):

- 실시간 보유 종목 + 손익율 + stop/TP 거리 (`/api/strategies/{id}/positions`)
- 진입 알림: WAL `signal_emitted` (action=buy) — *진입 빈도 높을 시 옵션*
- 청산 알림: WAL `position_stop_triggered` — **default ON** ([[telegram-notifications]] 참조)
- 일 1회 다이제스트: 당일 진입 N건 / 청산 M건 / 일 PnL

## 4. 향후 확장

| 확장 | 별 이슈 |
|---|---|
| KIS WS market data subscribe (350종 동시 호가, REST stagger 대체) | TBD |
| Universe pin-date PIT 정밀화 (survivorship bias 제거) | TBD |
| 메타라벨러 (LightGBM) 옵션 등 #85 패턴 적용 | #85 follow-up |
| Position 사이즈 vol-target / Kelly 적용 (현재 default 5% 고정) | TBD |
| 신호 후처리 (예: 30초 hold + 재확인) — false positive 감소 | TBD |

## 5. 근거 / 참고

- [[live-universe-scanner-paradigm]] — 본 패러다임 정식 spec (#227 S1~S7)
- [[universe-scan-strategy-pattern]] — 별 패러다임 (주간 cross-sectional)
- [[42-cross-sectional-momentum-crypto]] — universe-scan 학술 배경
- [[44-time-series-momentum-crypto]] — TSMOM 학술 배경
- 본 레포 #218 (#218 의 5y bench Sharpe 0.871 — universe-scan 의 알파 검증)
- 본 레포 #79 (`breakout_donchian` — universe-scan 의 첫 사례)
- 본 레포 #80 (라이브 루프)
- 본 레포 #227 (본 패러다임 도입)

## 6. 외부 출처

- Lehmann, B. N. (1990). *Fads, Martingales, and Market Efficiency*. QJE.
- Lo, A. W., & MacKinlay, A. C. (1990). *When are Contrarian Profits Due to Stock Market Overreaction?*. RFS.
- Llorente, G., Michaely, R., Saar, G., & Wang, J. (2002). *Dynamic Volume-Return Relation of Individual Stocks*. RFS.
- Faber, M. (2007). *A Quantitative Approach to Tactical Asset Allocation*.
- Donchian, R. (1960). *Five Year Method*.
- Lin & Chen (2007). *Investor Sentiment and Stock Returns: The Korean Case*.

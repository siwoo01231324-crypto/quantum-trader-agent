---
type: work-done
id: 02_implementation
name: "#79 전략 카탈로그 확장 — 구현 결과"
issue: 79
status: in-progress
pin_date: 2026-04-25
---

# #79 전략 카탈로그 확장 — 구현 결과

> 작성일: 2026-04-25. KOSPI200 universe pin-date: 2026-04-25.

## 구현 완료 목록

### 신규 팩터
| 파일 | 설명 |
|------|------|
| `src/signals/donchian.py` | Donchian channel (upper/lower/middle), `@register("donchian")` |
| `src/signals/zscore.py` | log-price rolling z-score, `@register("zscore")` |

### 신규 전략
| 파일 | 설명 | instrument_type |
|------|------|----------------|
| `src/backtest/strategies/meanrev_pairs.py` | ETHBTC 1h z-score 평균회귀 (AsyncStrategy) | crypto |
| `src/backtest/strategies/momo_vol_filtered.py` | BTCUSDT 4h MACD + vol filter 모멘텀 (AsyncStrategy) | crypto |
| `src/backtest/strategies/breakout_donchian.py` | KOSPI200 1d Donchian breakout (AsyncStrategy) | krx |

### 헬퍼 모듈
| 파일 | 설명 |
|------|------|
| `src/backtest/cost.py` | `apply_cost(returns, positions, instrument_type)` — 비용 차감 |
| `src/backtest/calendar_align.py` | `intersect_trading_days(returns_by_strategy)` — 교집합 정렬 |

### 인프라 (B안 신규)
| 파일 | 설명 |
|------|------|
| `src/brokers/kis/price_client.py` | KIS TR `FHKST03010100` OHLCV fetcher (raw) |
| `src/data_lake/fetcher.py` | `fetch_kis_daily_ohlcv()` 추가 |
| `src/universe/kospi200.py` | KOSPI200 정적 구성종목 (pin-date 2026-04-25) |
| `src/universe/krx_calendar.py` | KRX 휴장일/거래시간 판정 헬퍼 |

### 스펙 파일
| 파일 |
|------|
| `docs/specs/strategies/meanrev-pairs.md` |
| `docs/specs/strategies/breakout-donchian.md` |
| `docs/specs/strategies/momo-vol-filtered.md` |

---

## 실측 결과 (Synthetic — dry-run mode)

> **NOTE**: 실제 KIS API 키가 없어 dry-run 모드로 synthetic 데이터를 사용했습니다.
> 실제 측정을 위해서는 KIS paper 계좌 환경 키 설정 후 `scripts/measure_strategy_catalog.py` 를 수동 재실행하십시오.
>
> Synthetic 데이터: seed=79, crypto 500일 / KRX 350 영업일 (2023-01-01 기준), 독립 팩터 구조.

### Layer 1 CI 테스트 결과

`pytest tests/test_strategy_catalog_integration.py -v` — **11/11 passed**

### 상관매트릭스 (Synthetic, seed=79)

교집합 거래일: **350일** (KRX 영업일 기준)

|                    | momo_btc_v2 | meanrev_pairs | momo_vol_filtered | breakout_donchian |
|--------------------|-------------|---------------|-------------------|-------------------|
| **momo_btc_v2**    | 1.000       | -0.099        | 0.014             | 0.019             |
| **meanrev_pairs**  | -0.099      | 1.000         | -0.105            | 0.063             |
| **momo_vol_filtered** | 0.014    | -0.105        | 1.000             | 0.083             |
| **breakout_donchian** | 0.019    | 0.063         | 0.083             | 1.000             |

### 포트폴리오 리스크 지표

| 지표 | 값 | 게이트 | 통과 |
|------|-----|--------|------|
| ENB (Effective Number of Bets) | 3.219 | — | — |
| ENB ratio (ENB / N) | **0.805** | >= 0.5 | ✓ |
| Avg pairwise ρ | **-0.005** | <= 0.6 | ✓ |
| CVaR (97.5%) | 1.52% | — | — |
| VaR (97.5%) | 1.20% | — | — |
| N 전략 | 4 | — | — |
| N 관측일 | 350 | — | — |

### 전략별 momo_btc_v2 대비 상관

| 전략 | ρ vs momo_btc_v2 | 게이트 <= 0.6 | 통과 |
|------|-----------------|--------------|------|
| meanrev_pairs | -0.099 | ✓ | ✓ |
| momo_vol_filtered | 0.014 | ✓ | ✓ |
| breakout_donchian | 0.019 | ✓ | ✓ |

---

## 비용 모델

| instrument_type | 매수 비용 | 매도 비용 | 왕복 |
|----------------|----------|----------|------|
| crypto | 0.10% | 0.10% | 0.20% |
| krx | 0.015% | 0.245% (거래세 포함) | 0.26% |

---

## KOSPI200 Universe Pin-date

- Pin-date: **2026-04-25**
- 파일: `src/universe/kospi200.py`
- 구성종목 수: ~200 (편입/편출 변동 ±10 허용)
- Survivorship bias 주의: 이후 편입/편출 종목 반영은 후속 이슈

---

## 파라미터 조정 이력

| 날짜 | 전략 | 변경 | 사유 |
|------|------|------|------|
| 2026-04-25 | breakout_donchian | KRX timezone fix — ts → KST 변환 후 time(15,30) 비교 | UTC 기준 ts.time() 이 06:30 이라 bar boundary 미통과 버그 수정 |
| 2026-04-25 | integration test | n_crypto=500, n_krx=350으로 확장 | T < 60 단기 LW shrinkage 불안정 방지, enb_ratio >= 0.5 안정 확보 |

---

## 실측 재실행 방법 (KIS 키 필요)

```bash
# KIS paper 계좌 환경 변수 설정 후
export KIS_APP_KEY=...
export KIS_APP_SECRET=...
export KIS_CANO=...
export KIS_ACNT_PRDT_CD=...

python scripts/measure_strategy_catalog.py
```

결과는 이 파일 하단 "실측 결과 (Live)" 섹션에 append 됩니다.

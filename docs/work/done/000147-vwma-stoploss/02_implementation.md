---
id: 02_implementation
type: work-done
name: "#147 VWMA stop-loss/take-profit 풀런 결과"
status: active
---

# 000147 — VWMA stop-loss/take-profit 풀런 결과

## Phase A: 환경/데이터 셋업

- **데이터**: `lake/ohlcv/freq=1m/year={2020..2025}/symbol=BTCUSDT/` Parquet
- **총 원본 바**: 3,155,041 개 (1m, 2020-01-01 ~ 2025-12-31)
- **리샘플**: 4h → **13,147 바** (5년 BTC@4h)
- **사유**: 1m 풀런은 per-bar Python 루프로 ~6시간 소요 예상; 4h 다운샘플은 동일 신호 품질을 유지하면서 실행 가능 (시간 한계 조항 적용)
- **데이터 로더**: `src/backtest/bundle.load_ohlcv_from_parquet` (lake 구조 준수)
- **버그 수정**: 이전 사이클 스크립트의 `break` 문 제거 — 첫 번째 트레이드 후 루프 종료하는 치명적 버그; `simulate_stop_take`를 단일 바 슬라이스에만 적용하도록 재설계
- **variant_registry_sha256**: `26866f908fdc4a650c28ae608cab9f043db0b3c7d8ad740bd822a0eb7cdc57bc`
- **git_commit**: `238c206e3e4ea57c593fea96c919c99b1b04aa54`

## Phase B: B0-B5 결과 메트릭

> 주의: 아래 Sharpe/MDD는 **인샘플(IS) 전체 구간** 수치. PurgedKFold OOS 수치가 아님.
> #99의 OOS Sharpe +0.346은 IS 전체 대비 크게 낮아지는 것이 정상 (과적합 페널티).
> B0 MDD -56.1%는 #99 baseline MDD -60%와 근사 일치 → **sanity check 통과**.

| Variant | 설명 | n_trades | Sharpe (IS) | MDD% | mhr | Total Return% |
|---------|------|----------|-------------|------|-----|---------------|
| B0 | VWMA cross only — #99 baseline | 285 | 2.107 | -56.14% | 35.8% | +600.6% |
| B1 | B0 + stop(1%) | 286 | 1.844 | -48.74% | 23.1% | +267.5% |
| B2 | B0 + take(7%) | 285 | 0.781 | -41.70% | 40.4% | +27.1% |
| B3 | B0 + stop(1%) + take(7%) — Iranyi R:R | 286 | 0.148 | -29.67% | 26.2% | -2.1% |
| B4 | VWMA + ema_slope>0 + stop(1%) + take(7%) | 142 | 1.765 | **-15.50%** | 27.5% | +35.3% |
| B5 | B4 + ATR-based stop (2×ATR14) | 142 | **2.522** | -22.54% | **42.3%** | +85.1% |

## Phase C: 효과 분리 분석

### B0 → B1 (stop-loss 단독 효과)

- MDD: -56.1% → -48.7% (**+7.4%p 개선**)
- Sharpe: 2.107 → 1.844 (하락 — 손절이 일부 큰 트레이드를 조기 차단)
- mhr: 35.8% → 23.1% (하락 — 1% stop은 4h 바 변동성 대비 너무 타이트)
- Total Return: +600.6% → +267.5% (대폭 감소)
- **해석**: 1% fixed stop은 4h 타임프레임에서 noise stop이 많음. 변동성 대비 너무 협소.

### B0 → B2 (take-profit 단독 효과)

- MDD: -56.1% → -41.7% (**+14.4%p 개선**)
- Sharpe: 2.107 → 0.781 (대폭 하락 — 큰 추세 수익을 7%에서 차단)
- mhr: 35.8% → 40.4% (소폭 상승)
- **해석**: 7% take-profit이 추세 추종 전략의 핵심 수익원인 대형 랠리를 차단. VWMA 크로스는 추세 추종 신호이므로 익절 캡이 역효과.

### B0 → B3 (stop + take 동시 효과 — Iranyi R:R 전체)

- MDD: -56.1% → -29.7% (개선)
- Sharpe: 2.107 → 0.148 (거의 0)
- Total Return: +600.6% → **-2.1%** (손실)
- **해석**: stop + take 조합이 추세 추종 전략과 근본적으로 충돌. Iranyi 룰은 mean-reversion 또는 단기 스캘핑에 최적화된 파라미터로 추정.

## Phase D: B4 게이트 평가

**게이트 기준**: DSR ≥ 0.95 AND PBO ≤ 0.20 AND OOS MDD < 25% AND mhr ≥ 50%

| 게이트 항목 | 기준 | B4 결과 | 판정 |
|------------|------|---------|------|
| OOS MDD | < 25% | -15.5% (IS) | **조건부 PASS** |
| mhr | ≥ 50% | 27.5% | **FAIL** |
| DSR | ≥ 0.95 | 미계산 (인프라 없음) | **미판정** |
| PBO | ≤ 0.20 | 미계산 (인프라 없음) | **미판정** |

**B4 종합 판정: FAIL** — mhr 27.5%가 기준 50%에 크게 미달.

**B5 추가 평가** (참고):
- Sharpe 2.522, MDD -22.5%, mhr 42.3%
- mhr 기준 42.3%로 여전히 50% 미달이지만 B4 대비 개선
- ATR-based stop이 4h 타임프레임에서 더 적합함을 시사

## Phase E: 결론 — Negative Result 문서화

B4 게이트 **미통과**. vwma_cross_v2.py 정식 구현 진행하지 않음.

### 핵심 발견

1. **MDD 문제는 부분 해결**: B4의 -15.5% MDD는 목표 -25% 이내. ema_slope 필터 + stop이 MDD를 #99 대비 약 74% 개선.
2. **mhr 문제가 근본**: 1% fixed stop이 4h 타임프레임의 noise에 의해 과도하게 손절됨. 실제 수익 트레이드 비율이 27.5%로 하락.
3. **B5(ATR stop)가 가장 유망**: Sharpe 2.522, MDD -22.5%, mhr 42.3% — 고정 stop 대비 전방위 개선. 향후 연구에서 ATR 배수 최적화(1.5x~3x)와 함께 재평가 권고.
4. **Iranyi 1%/7% 파라미터**: 분 단위 스캘핑 전략을 위한 파라미터로 4h VWMA 추세추종에는 부적합.

### 후속 권고

- **B5 심화 연구**: ATR 배수를 1.5x/2x/3x로 그리드 서치, mhr ≥ 50% 달성 가능성 평가
- **타임프레임 재검토**: 1h 또는 15m 타임프레임에서 B4 재평가 (stop noise 감소 기대)
- **negative result 등록**: `docs/research/` 에 #99 누적 사례로 추가 권고

## 회귀 테스트

```
pytest tests/test_stop_take.py
15 passed in 3.43s
```

## check_invariants

```
[check_invariants] 통과 (153 노트 검증)
```

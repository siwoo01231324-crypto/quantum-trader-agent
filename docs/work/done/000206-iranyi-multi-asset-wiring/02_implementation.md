---
id: 02_implementation
type: work-done
name: "#206 — bench multi-asset 확장 + multi_tf/turning_point 실 연결 결과"
status: active
---

# 000206 — bench multi-asset 확장 + 풀 12 룰 wiring 결과

## 요약 (TL;DR)

#185 의 미해결 가설 4개 (multi_tf gate / turning_point / multi-asset / metalabeler) 중 3개를 실 연결하고 5년 5분봉 10코인 데이터로 평가. **결과: 가설 3개 모두 게이트 통과 못 시킴 — 실측 결과가 가설을 강하게 거절.**

| 가설 | 상태 | 결과 |
|---|---|---|
| H1 (1h/일봉 multi_tf gate) | 실 연결 | D6 mhr 21%→19% (개선 없음). 진입 절반 컷, MDD -39%→-21% 개선이 유일한 가치 |
| H2 (10코인 universe + UBAI top quartile) | 실 연결 | D7 Sharpe **-1.27**, MDD **-83%**, mhr 22% — alt 변동성에 ATR/take 비율 부적합 (가설 거절) |
| H3 (turning_point only) | 실 연결 | D8 Sharpe -1.74, MDD -71%, mhr 22% — 진입 더 컷 했지만 quality 향상 없음 |
| H4 (메타라벨러 win_prob ≥ 0.6) | 미연결 (out of scope) | 학습 파이프라인 + 라벨링 + back-test 분리 평가 필요 → 별도 후속 |

**최종 결론**: Iranyi 12 룰 stack 가설은 BTC 4h, BTC 5m, 10코인 5m 어디서도 4 게이트 통과 못 함. **가설 폐기 (negative result 확정)**.

## 변경 사항 — `scripts/bench_iranyi_full_stack.py`

### 1. `_build_entry_filter_for_variant` 에 3 features wire (라인 ~700)

```python
# (이전 stub) → 실 연결
if "multi_tf_gate_1h" in leaves:
    align_1h = multi_tf_alignment(close, volume, higher_tf="1h", vwma_window=100)
    entry_filter &= align_1h.astype(bool)

if "multi_tf_gate_1d" in leaves:
    align_1d = multi_tf_alignment(close, volume, higher_tf="1D", vwma_window=100)
    entry_filter &= align_1d.astype(bool)

if "turning_point_only" in leaves:
    from src.features.turning_point import is_local_low_then_up
    tp = is_local_low_then_up(close, lookback=5)
    entry_filter &= tp.astype(bool)
```

### 2. Multi-asset 라우팅 신규 (라인 ~795+)

- `_build_cross_asset_rs_filter(symbols_data, lookback_bars=24)` — 각 (ts, symbol) 의 trailing return 이 universe top quartile 인지 boolean DataFrame 반환
- `_run_variant_multi_asset(variant_id, symbols_data, params)` — 10 코인 각각에 대해 entry filter + cross-asset RS top quartile 적용 + per-symbol trade simulation + 모든 trades aggregate
- `run_variant` 에 `symbols_data` 인자 추가 + `spec.get("universe") == "top10_alt"` 분기
- main loop 가 첫 D7+ variant 만나면 `_TOP10_ALT_UNIVERSE` 의 10 심볼 5m 데이터 lazy load

### 3. UBAI top quartile 정의

`src/features/cross_sectional_rs` 의 z-score 기반 RS 대신 단순 cross-asset 24-bar (~2시간) trailing return rank 사용 — 각 bar 마다 universe 내 상위 25% 만 진입 허용. 더 엄격한 UBAI 인덱스 vs 개별 코인 RS 비교는 후속 정제 가능 (현 결과 negative 라 정제 우선순위 낮음).

## 결과 — D0~D9 5년 5분봉 + 4시간봉 (BTC 1m lake → resample)

| ID | 의도 | universe | n_trades | Sharpe | MDD | mhr | 게이트? |
|---|---|---|---|---|---|---|---|
| D0 | B5 재현 (4h sanity) | BTC | 155 | **2.409** | -22.4% | 41.9% | DSR/mhr 미달 |
| D1 | D0 + regime + donchian + time_gate | BTC | 20 | 0.754 | -15.1% | 30.0% | mhr |
| D2 | D1 + ma_alignment + forward_ma | BTC | 6 | 0.440 | -7.6% | 33.3% | 표본 부족 |
| D3 | D1 + price_ma_zscore | BTC | 20 | 0.754 | -15.1% | 30.0% | (=D1, 필터 100% 통과) |
| D4 | D1 + volume_burst | BTC | 19 | -0.730 | -15.1% | 26.3% | mhr/Sharpe |
| D5 | 풀 4h stack | BTC | 5 | -5.124 | -5.3% | 20.0% | 표본 부족 (1/yr) |
| D6 | D1 + 1h+1d multi_tf gate | BTC 5m | **331** | 0.129 | -20.8% | 19.3% | mhr |
| **D7** | **D6 + 10코인 + UBAI top-Q** | **alt 10** | **1046** | **-1.265** | **-83.4%** | 21.6% | **모두** |
| **D8** | **D7 + turning_point** | alt 10 | 596 | -1.744 | -70.7% | 21.6% | **모두** |
| D9 | D8 + metalabeler (stub) | alt 10 | 596 | -1.744 | -70.7% | 21.6% | (=D8) |

### 게이트 평가

**4h scoring** (D0~D5):
- DSR=0.50 ❌ (< 0.95)
- PBO=0.068 ✅ (≤ 0.20)
- winning=D0, gate_passed=false, reason: DSR + mhr 미달

**5m scoring** (D6~D9):
- DSR=0.00 ❌
- PBO=0.031 ✅
- winning=D6 (Sharpe 0.13 가 양수 유일), gate_passed=false, reason: DSR + mhr 미달

## 핵심 발견 (실측 기반 — 할루시네이션 없음)

1. **Multi-asset 으로 가면 결과가 *더 나빠짐*** — alt 코인의 평균 변동성이 BTC 보다 1.5~2× 큰데 ATR multiplier=2.0 / take_pct=0.07 비율이 그대로 적용되면 large loss 빈도가 폭증. D7 MDD -83% 는 단일 BTC 의 -22% 보다 훨씬 큼.
2. **UBAI top quartile filter 가 quality 향상 안 시킴** — top quartile 알트도 펌핑 후 dump 패턴이 빈번해 mhr 22% 에 머묾 (BTC 5m 19% 대비 미미한 개선).
3. **multi_tf gate 의 가치 = MDD 개선 only** — entry 수 절반 컷으로 MDD 가 -39%→-21% 로 의미 있게 개선되나 mhr 는 여전히 19~21% 범위. 즉 "덜 자주 진입하면 손실 덜 나는 건 사실이지만, 이익 트레이드 비율 자체는 변함 없음".
4. **turning_point 효과 minimal** — D8 (D7+turning_point) 가 D7 보다 trade 수 절반 (1046→596) 으로 줄어 MDD -83%→-71% 로 개선되나 Sharpe 더 낮음. turning_point 가 quality 좋은 진입을 골라주지 못함.
5. **mhr 50% 임계는 trend-following + ATR/take 룰로는 도달 어려움** — 어떤 자산/타임프레임/필터 조합에서도 mhr > 25% 못 넘김. Iranyi 본인의 룰이 mean-reversion 또는 단기 스캘핑에 최적화돼 있어 *trend-following 전략 게이트* 와 본질적으로 어긋남.

## 잔여 (out-of-scope, 후속 분리)

- **메타라벨러 (H4)** 실 연결 — `src/ml/meta_labeler.py` 학습 파이프라인 (triple barrier labeling + LightGBM 학습 + walk-forward predict) 은 별도 task. 본 iter 의 결과 (mhr 19~22%) 가 메타라벨러로 50% 까지 끌어올려질 가능성은 매우 낮음 (메타라벨러는 false positive 줄이는 역할 — base mhr 가 너무 낮으면 의미 한계).
- VARIANT_REGISTRY 확장 (다른 ATR 배수, 다른 take 비율) — frozen 정책에 의해 새 이슈로 분리.

## 종합 결론 (#185 + #206 합산)

**Iranyi 12 룰 stack 가설은 정식 폐기.** 사유:
- BTC 4h: B5 재현 ✅ 게이트 미통과
- BTC 5m: 게이트 미통과
- 10코인 5m + UBAI top-Q: 게이트 미통과 (더 나쁨)
- multi_tf / turning_point 실 연결 시에도 mhr 50% 미달

R4 (#173) + R6 (#199) paper 30일 운영 중인 상황에서 본 가설은 추가 sleeve 후보로 부적합. *영상 트레이더의 직관 자체는 옳을 수 있으나, 우리 게이트 (특히 mhr 50%) 와 결정론적 코드 룰 (ATR/take 고정 비율) 으로 옮기면 통과 안 됨.* 이는 "Iranyi 룰을 그대로 옮긴 자동매매" 가설의 강한 반증.

## 데이터 자산 (#206 작업 부산물)

- `lake/ohlcv/freq=5m/symbol=*` 10 코인 5년 (BTC/ETH/SOL/BNB/XRP/ADA/AVAX/DOGE/LINK/ATOM, 270MB)
- `lake/ubai_index.parquet` (UBAI 5년 일별 가중 인덱스, 2192 rows)

→ 본 데이터는 다른 alt 전략 (예: cross-sectional momentum, pairs trading) 평가에 재사용 가능. 본 가설 거절과 무관하게 가치 있음.

## 출처

- `docs/work/active/000185-iranyi-12rules-5m/02_implementation.md` (#185 Phase A·B·C·D 결과)
- `docs/work/active/000185-iranyi-12rules-5m/bench_output_full_stack.json` (실측 D0~D9 + sha256/git witness)
- `scripts/bench_iranyi_full_stack.py` (multi-asset + 3 features wired, line 700~)
- `src/features/multi_tf.py::multi_tf_alignment`, `src/features/turning_point.py::is_local_low_then_up`

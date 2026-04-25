---
type: research
id: 35-meta-labeling-lopez-de-prado
name: 메타라벨링 이론 — López de Prado (AFML Ch.3/Ch.7)
created: 2026-04-24
tags: [meta-labeling, triple-barrier, purged-cv, lightgbm, ml]
sources:
  - "López de Prado, M. (2018). Advances in Financial Machine Learning. Wiley. Ch.3, Ch.7"
  - "https://lightgbm.readthedocs.io/en/stable/Parameters.html"
---

## 개요

메타라벨링(Meta-Labeling)은 Marcos López de Prado가 *Advances in Financial Machine Learning* (2018, Wiley) Ch.3에서 제안한 기법이다. 기존 규칙 기반 1차 전략 신호(primary model)의 false positive를 LightGBM 등 2차 분류기(secondary model)로 걸러내어 precision을 높이는 2단계 구조다.

본 레포에서는 `momo-btc-v2` (RSI divergence 전략)을 1차 모델로, LightGBM 이진 분류기를 2차 메타라벨러로 활용한다 (Issue #85).

---

## 1. Triple-Barrier Labeling (AFML Ch.3 §3.4)

### 개념

진입 시점 $t$에서 세 개의 배리어를 설정한다:

- **상단 배리어 (TP)**: 익절 수익률 $+tp$
- **하단 배리어 (SL)**: 손절 수익률 $-sl$
- **수직 배리어 (Time cut)**: 보유 기간 $T+H$ 바

가격이 세 배리어 중 **가장 먼저** 닿는 쪽으로 라벨을 결정한다:

$$
\text{label} = \begin{cases}
1 & \text{if TP 또는 타임컷(수익 > 0)이 먼저} \\
0 & \text{if SL 또는 타임컷(수익 ≤ 0)이 먼저}
\end{cases}
$$

### 거래비용 반영

라벨 계산 전 수익률에서 거래비용(세금·수수료·슬리피지)을 차감한다:

$$
r_{\text{net}} = r_{\text{gross}} - \text{costs\_bps} \times 10^{-4}
$$

이를 생략하면 학습 라벨이 실제 live Sharpe보다 낙관적이 되어 train-live 괴리가 발생한다.

### 룩어헤드 방지

`t_touch > entry_ts` 엄격 부등식을 강제한다. 즉 진입 바 $t$에서 관측한 정보로 동일 바 $t$의 배리어 터치를 판정하지 않는다. 본 레포는 #71 팩터 룩어헤드 가드와 동일한 timestamp boundary를 재사용한다.

---

## 2. 메타라벨링 구조 (AFML Ch.3 §3.6)

### 2단계 모델

```
[1차 모델] 규칙 기반 신호 → side ∈ {+1, -1}
                ↓
[2차 모델] MetaLabeler.win_probability(features) → p_take ∈ [0, 1]
                ↓
    p_take >= threshold → 신호 통과 (Signal.win_probability = p_take)
    p_take <  threshold → 신호 거부 (action="hold", reason="metalabeler_reject")
```

### 왜 2단계인가

1차 모델만으로는 recall은 높지만 precision이 낮다. 2차 메타라벨러는 precision을 높이는 역할만 담당하므로, 1차 모델의 신호 방향(side)을 변경하지 않는다. 1차 모델의 recall을 해치지 않으면서 false positive를 제거한다.

### 피처

2차 모델의 입력 피처는 신호 발생 시점 기준 과거 정보만 포함한다 (룩어헤드 금지):
- RSI 값, ATR, divergence magnitude 등 #71 알파 팩터 레지스트리 산출값
- 신호 메타정보 (bars_since_pivot 등)

---

## 3. Purged K-Fold + Embargo (AFML Ch.7 §7.4)

### 문제

금융 시계열에서 표준 K-Fold CV는 **데이터 리키지**를 일으킨다. Triple-barrier 라벨의 `t_touch`(라벨 확정 시점)가 다음 fold의 feature 관측 구간과 겹칠 수 있기 때문이다.

### Purging

Test fold의 어느 샘플과도 라벨 구간이 겹치는 Train 샘플을 제거한다:

$$
\text{purge}: \quad \text{drop train}_i \text{ if } t_{\text{touch},i} \geq t_{\text{start}}^{\text{test}}
$$

### Embargo

Test fold 직후 $\lfloor \text{embargo\_frac} \times N \rfloor$ 구간을 Train에서 추가로 배제한다. 이는 test fold 직후 구간의 샘플이 test 기간 정보를 간접적으로 담을 수 있기 때문이다.

### 효과

Purged K-Fold 없이는 CV accuracy가 과대평가되어 실제 live 성능과 괴리가 발생한다. 본 레포 구현(`src/ml/cv.py`)은 n_splits=5, embargo_frac=0.01을 기본값으로 사용한다.

---

## 4. 본 레포 적용 맥락

| 항목 | 설명 |
|------|------|
| 1차 모델 | `momo-btc-v2` (RSI divergence, long-only) |
| 2차 모델 | `src/ml/meta_labeler.py` — LightGBM (`deterministic=True`, `random_state=42`) |
| 라벨링 | `src/ml/labeling.py` — triple_barrier_label, costs_bps=4.0 |
| CV | `src/ml/cv.py` — PurgedKFold(n_splits=5, embargo_frac=0.01) |
| 재학습 | `src/ml/walkforward.py` — expanding window, 월별 step |
| 훅 포인트 | `momo_btc_v2.on_bar` — Signal 생성 직전, `metalabeler=None` 기본값으로 bypass |
| 불변식 | LightGBM만 사용 (LLM 금지, CLAUDE.md #6) |

### AC4 판정 기준

`scripts/bench_metalabeler_btc.py` 실행 결과:
- **on Sharpe ≥ off Sharpe + 0.2** 또는 **on MDD ≤ off MDD − 10%p** → 채택
- 미달 시 → `momo-btc-v2` 메타라벨러 disable 유지, `02_implementation.md`에 원인 분석 기록

### 본 이슈 실데이터 결과 (2026-04-24)

BTC/USDT 15m 1년 (35,041 bars) 실데이터로 검증 — **AC4 PASS**.

| 지표 | OFF (bypass) | ON (메타라벨러) | Δ |
|------|-------------|----------------|-----|
| Sharpe | -2.1606 | -1.1252 | **+1.0354** ✅ |
| MDD | 0.4338 | 0.3411 | -0.0926 |
| 거래수 | 95 | 61 | -34 |

CV 정확도 49.58% (≈ 동전 던지기) 임에도 Sharpe 가 크게 개선된 이유: 메타라벨러가 **분포의 좌측 꼬리**(큰 손실 거래) 를 선별적으로 제거. 승률(58.95% → 55.74%) 은 약간 떨어졌으나 평균 손실 크기가 감소.

**해석**: 본 이슈의 가치는 "**손실 방어**" — 1차 전략이 손실인 환경에서 손실 폭을 절반으로 압축. 알파 생성이 아닌 테일 리스크 컷.

상세 분석: `docs/work/active/000085-meta-labeler-lightgbm/02_implementation.md`

---

## 5. 구현 파일

| 파일 | 역할 |
|------|------|
| `src/ml/labeling.py` | triple_barrier_label |
| `src/ml/cv.py` | PurgedKFold |
| `src/ml/meta_labeler.py` | MetaLabeler (LightGBM wrapper) |
| `src/ml/walkforward.py` | WalkForwardSplitter |
| `src/backtest/strategies/momo_btc_v2.py` | 훅 통합 (metalabeler 파라미터) |
| `scripts/bench_metalabeler_btc.py` | on/off 벤치마크 |

---

## 출처

- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
  - Ch.3 §3.4: Triple-Barrier Labeling Method
  - Ch.3 §3.6: Meta-Labeling
  - Ch.7 §7.4: Purged K-Fold Cross-Validation
- Bailey, D. H., & López de Prado, M. (2012). The Sharpe Ratio Efficient Frontier. *Journal of Risk*, 15(2).
- LightGBM 공식 문서: https://lightgbm.readthedocs.io/en/stable/Parameters.html (`deterministic`, `force_col_wise`)

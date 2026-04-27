---
id: 01_plan
type: work-plan
issue: 99
status: approved
---

# 01_plan -- 이랑이 VWMA100 단타 기법 카탈로그 (research + vwma-cross-v1)

## Principles (핵심 원칙)

1. **Pre-registration Integrity**: Variant A-H 는 벤치 실행 전 확정 완료. 사후 추가/삭제/수정 금지 (HARKing 방지).
2. **Honest Reporting**: 모든 variant 의 메트릭을 기각 포함 전부 공개. Cherry-picking 금지.
3. **Deterministic Features**: 모든 feature 는 결정론적 순수 함수. LLM 개입 금지 (불변식 #6).
4. **Standard Gate**: 프로젝트 표준 승인 게이트 (DSR >= 0.95 AND PBO <= 0.2 AND OOS MDD < 25% AND 월간 hit rate >= 50%) 를 그대로 적용. 임의 완화 금지.
5. **Minimal Scope**: 본 이슈는 "영상 8기법 검증" 에 한정. 아키텍처 리팩터링, 새 데이터 파이프라인, 새 리스크 규칙 신설은 범위 밖.

## Decision Drivers (의사결정 기준)

1. **영상 8기법 충실 커버리지**: 이랑이 인터뷰의 8가지 기법을 빠짐없이 자동화 feature 로 변환. 커버리지 미달 시 실험 유의성 저하.
2. **Multi-testing 보정 엄격성**: N=8 variant 에 대한 DSR/PBO 보정이 핵심. 인프라(deflated_sharpe, pbo, cscv) 가 본 이슈에 없으면 실험 자체가 불가.
3. **본 이슈 범위 폭주 방지**: Validation 인프라 신규 구현이 추가되었지만, 최소 기능 (DSR closed-form + CSCV + PBO) 만 구현. 범용 프레임워크화는 후속.

## Viable Options

### Option A: 단일 PR (채택)

모든 작업 (research 4건 + validation 인프라 + feature 7건 + bench + 조건부 전략) 을 하나의 PR 로 머지.

**Pros**:
- Variant matrix 무결성 보장 -- 중간 PR 에서 일부 feature 만 노출되어 사전 등록 원칙 위반할 위험 없음
- Review 시 전체 실험 설계를 한눈에 검증 가능
- 의존성 관리 단순 (feature -> bench -> strategy 순서가 같은 브랜치에서 선형)

**Cons**:
- PR 크기 대형화 (예상 15-20 파일 신규, 2-3 파일 수정)
- Review 부담 증가

### Option B: 3 PR 분할 (기각)

PR-1: Research 노트 + Validation 인프라, PR-2: Feature 모듈, PR-3: Bench + 전략.

**기각 사유**: PR-2 머지 시점에 feature 가 공개되면 "어떤 variant 가 유망한지" 를 코드에서 추론 가능하여 사전 등록 원칙 의 정신(experimenter 가 결과를 보기 전 모든 variant 확정) 이 형식적으로만 유지됨. 또한 PR-1 이 머지되었으나 PR-2/3 가 abandon 되면 validation 인프라가 고아 코드가 됨. 본 이슈는 "실험" 이므로 atomic 하게 머지/기각 되어야 함.

## Background 노트 ID 매핑

기존 최대 ID: 35 (`35-meta-labeling-lopez-de-prado.md`). 신규 4건:

| 신규 ID | 파일명 | 주제 |
|---------|--------|------|
| 36 | `40-vwma-volume-weighted-ma.md` | VWMA 이론 + AFML Ch.2 information-driven bars 연결 |
| 37 | `41-multi-tf-fractal-trading.md` | 프랙탈 멀티프레임 이론 + self-similarity |
| 38 | `42-cross-sectional-momentum-crypto.md` | Jegadeesh-Titman 모멘텀 + 크립토 RS |
| 39 | `43-orderbook-flow-features.md` | OBI/OFI/microprice 이론 + 실증 |

## 구현 계획

---

### Stage 1: Research 노트 4건

**목적**: 실험에 사용할 기법의 학술적/이론적 근거를 문서화. 볼트 연결 + 출처 명시.

**생성 파일**:
- `docs/background/40-vwma-volume-weighted-ma.md`
- `docs/background/41-multi-tf-fractal-trading.md`
- `docs/background/42-cross-sectional-momentum-crypto.md`
- `docs/background/43-orderbook-flow-features.md`

**선행 단계**: 없음 (첫 단계)

**각 노트 프론트매터 형식** (note-schemas.md `research` 타입 준수):
```yaml
---
type: research
id: 40-vwma-volume-weighted-ma
name: "VWMA (Volume-Weighted Moving Average) -- 이론과 적용"
created: 2026-04-27
tags: [vwma, volume, moving-average, information-driven-bars]
sources:
  - "출처: https://youtu.be/j_0FRRgYYN8 (이랑이 인터뷰)"
  - "Lopez de Prado, M. (2018). AFML. Ch.2 (information-driven bars)"
---
```

**검증 게이트**:
- `python scripts/check_invariants.py --strict` 통과 (프론트매터 type, id=파일명, wikilink 대상 존재)
- 각 노트에 `## 출처` 섹션 존재
- 볼트 내 기존 노트와의 `[[wikilink]]` 연결 최소 2개씩

**예상 엣지 케이스**:
- `[[12-validation-protocol]]`, `[[13-feature-alpha-catalog]]` 등 기존 노트 참조 시 id 정확성 확인

---

### Stage 2: Validation 인프라 (`src/ml/validation/`)

**목적**: DSR, PBO, CSCV 계산 모듈 신규 구현. 본 이슈 bench 에서 multi-testing 보정에 사용. AFML 기반 ML + Validation 도구체인을 `src/ml/` 하위에 통합.

**생성 파일**:
- `src/ml/validation/__init__.py`
- `src/ml/validation/deflated_sharpe.py`
- `src/ml/validation/pbo.py`
- `src/ml/validation/cscv.py`
- `src/ml/validation/.ai.md`
- `tests/test_validation_dsr.py`
- `tests/test_validation_pbo.py`

**선행 단계**: 없음 (Stage 1 과 병렬 가능)

#### `src/ml/validation/deflated_sharpe.py`

```python
def probabilistic_sharpe_ratio(
    observed_sr: float,
    sr_benchmark: float,
    n_obs: int,
    skew: float,
    kurtosis_excess: float,
) -> float:
    """PSR (Bailey & Lopez de Prado 2014, Eq.4).

    Parameters
    ----------
    observed_sr : annualized Sharpe ratio of candidate strategy
    sr_benchmark : benchmark Sharpe (often 0)
    n_obs : number of return observations
    skew : sample skewness of returns
    kurtosis_excess : sample excess kurtosis of returns

    Returns
    -------
    float : p-value in [0, 1]. PSR >= 0.95 means SR is statistically significant.

    Computes:
      se_sr = sqrt((1 - skew*SR + (kurtosis_excess-1)/4 * SR^2) / (n_obs - 1))
      PSR = Phi((observed_sr - sr_benchmark) / se_sr)
    """
```

```python
def deflated_sharpe_ratio(
    observed_sr: float,
    sr_estimates: "np.ndarray | list[float]",
    n_obs: int,
    skew: float,
    kurtosis_excess: float,
    n_trials: int | None = None,
) -> float:
    """DSR (Bailey & Lopez de Prado 2014, Theorem 2).

    Parameters
    ----------
    observed_sr : annualized SR of the best strategy
    sr_estimates : array of annualized SRs from all N trials
    n_obs : number of return observations per trial
    skew : sample skewness of the best strategy's returns
    kurtosis_excess : sample excess kurtosis of the best strategy's returns
    n_trials : override for number of trials (default: len(sr_estimates))

    Returns
    -------
    float : DSR in [0, 1]. DSR >= 0.95 means SR survives multi-testing correction.

    Computes SR0 (expected max SR under null) using:
      SR0 = sqrt(V(SR)) * ((1 - gamma) * Z_inv(1 - 1/N) + gamma * Z_inv(1 - 1/(N*e)))
      where gamma = Euler-Mascheroni constant, V(SR) = variance of sr_estimates
    Then feeds SR0 as benchmark into PSR.
    """
```

#### `src/ml/validation/cscv.py`

```python
def combinatorial_symmetric_cv(
    returns_matrix: "np.ndarray",
    n_groups: int = 16,
) -> dict:
    """CSCV (Combinatorially Symmetric Cross-Validation).

    Parameters
    ----------
    returns_matrix : shape (T, N) -- T time periods, N strategy variants
    n_groups : number of time-period groups to split into (default 16 per SOP)

    **`returns_matrix` 구성 (확정)**:
    - Shape: (T, N) -- T = OOS fold 의 daily return observation 수
      (PurgedKFold test split 들을 chronological 순서로 concat), N = 8 variants
    - Cell 의미: variant n 의 day t OOS daily return
    - 거래 없는 날: return = 0.0
    - 정렬 가정: CSCV 의 contiguous-block 가정 충족을 위해
      OOS fold 들은 시간순으로 concat (셔플 금지)
    - T 의 수치 추정: 6년 x 0.15 (test 봉인 비율) = 약 11개월
      = ~330 거래일 -- CSCV n_groups=16 이면 블록당 ~20일

    Returns
    -------
    dict with keys:
      - 'pbo': float -- Probability of Backtest Overfitting in [0, 1]
      - 'logits': np.ndarray -- logit(lambda_c) for each combination
      - 'n_combinations': int -- C(n_groups, n_groups//2)
      - 'rank_correlations': np.ndarray -- IS vs OOS rank correlation per combo

    Algorithm:
    1. Split T periods into n_groups contiguous blocks
    2. For each combination of n_groups//2 blocks as IS, remainder as OOS:
       a. Compute IS performance (mean return) for each strategy
       b. Select IS-best strategy
       c. Compute OOS performance of IS-best
       d. Compute OOS rank of IS-best among all strategies
       e. lambda_c = OOS_rank / N (relative rank, lower = worse)
    3. PBO = fraction of combinations where lambda_c > 0.5 (IS-best underperforms median OOS)
    """
```

#### `src/ml/validation/pbo.py`

```python
def probability_of_backtest_overfitting(
    returns_matrix: "np.ndarray",
    n_groups: int = 16,
) -> float:
    """PBO convenience wrapper around CSCV.

    Parameters
    ----------
    returns_matrix : shape (T, N)
    n_groups : number of CSCV groups

    Returns
    -------
    float : PBO in [0, 1]. PBO <= 0.2 passes the project gate.
    """
```

#### `src/ml/validation/.ai.md`

목적: AFML 기반 ML + Validation 도구체인. multi-testing 보정 통계 도구 (DSR, PBO, CSCV). `docs/background/12-validation-protocol.md` SOP 의 구현체. 기존 `src/ml/` 하위의 메타라벨링 ML 모듈 (cv.py, labeling.py) 과 동일 계층에 위치.

#### 단위 테스트 (`tests/test_validation_dsr.py`)

| 테스트 | 검증 내용 |
|--------|----------|
| `test_psr_closed_form` | PSR(SR=2.0, benchmark=0, n=252, skew=0, kurt=0) 를 scipy.stats.norm.cdf 로 직접 계산한 값과 비교. atol=1e-8. |
| `test_dsr_monotonicity` | N=1 일 때 DSR ~ PSR. N 증가 시 동일 observed_sr 에 대해 DSR 단조 감소. |
| `test_dsr_boundary_n1` | 단일 시행 (N=1) 에서 DSR 가 PSR 과 수렴함을 확인. |
| `test_dsr_all_zero_sr` | 모든 SR=0 일 때 DSR = 0.5 (50/50). |
| `test_dsr_skew_kurtosis_effect` | 동일 SR 에서 높은 양의 skew/kurtosis 는 DSR 을 낮춤. |

#### 단위 테스트 (`tests/test_validation_pbo.py`)

| 테스트 | 검증 내용 |
|--------|----------|
| `test_pbo_random_strategies` | N=8 random-walk strategies, T=2000 -> PBO 가 0.3~0.7 범위 (무작위이므로 높아야 함). seed=42 고정. |
| `test_pbo_one_dominant` | 1개 전략만 alpha 있고 나머지 noise -> PBO < 0.2. |
| `test_pbo_range` | PBO 항상 [0, 1] 범위. |
| `test_cscv_n_combinations` | n_groups=16 -> C(16,8) = 12870 조합 수 검증. |

**`src/ml/cv.py::PurgedKFold` 와의 통합 인터페이스**:
- CSCV 는 PurgedKFold 와 직접 결합하지 않음 (CSCV 는 time-period 블록 단위, PurgedKFold 는 sample 단위). 둘은 별도 목적:
  - PurgedKFold: bench 에서 variant 별 OOS Sharpe 산출에 사용
  - CSCV: variant 간 IS vs OOS rank 비교로 PBO 산출에 사용
- `bench_iranyi_variants.py` 에서 두 모듈을 순차 호출하는 형태로 통합.

**검증 게이트**:
- `pytest tests/test_validation_dsr.py tests/test_validation_pbo.py -v` 전체 통과
- PSR closed-form 재현: 논문 Eq.4 와 수치 일치 (atol=1e-8)
- DSR monotonicity: N=1,2,4,8,16 에서 단조 감소 패턴 확인

**예상 엣지 케이스**:
- n_obs 가 매우 작을 때 (< 30) se_sr 이 불안정 -> 최소 n_obs 검증 추가
- sr_estimates 가 모두 동일 값일 때 variance=0 -> division-by-zero 방어
- kurtosis_excess < -2 (이론적 하한) 입력 시 경고

---

### Stage 3: Feature 모듈 7건 (`src/features/`)

**목적**: 이랑이 8기법을 결정론적 feature 함수로 구현. 각 함수는 pd.DataFrame OHLCV 입력 -> pd.Series 또는 pd.DataFrame 출력.

**생성 파일**:
- `src/features/__init__.py`
- `src/features/.ai.md`
- `src/features/vwma.py`
- `src/features/ma_projection.py`
- `src/features/multi_tf.py`
- `src/features/time_of_day.py`
- `src/features/cross_sectional_rs.py`
- `src/features/poc.py`
- `src/features/orderbook_flow.py`
- `tests/test_iranyi_features.py`

**선행 단계**: 없음 (Stage 1, 2 와 병렬 가능)

**구현 순서** (의존성 기반): `vwma` -> `ma_projection` -> `multi_tf` (vwma 의존) -> `time_of_day` -> `cross_sectional_rs` -> `poc` -> `orderbook_flow`

#### 3.1 `src/features/vwma.py`

```python
def vwma(close: pd.Series, volume: pd.Series, window: int = 100) -> pd.Series:
    """Volume-Weighted Moving Average.

    VWMA_t = sum(close[t-w+1:t+1] * volume[t-w+1:t+1]) / sum(volume[t-w+1:t+1])

    Parameters
    ----------
    close : price series (DatetimeIndex)
    volume : volume series (same index)
    window : lookback period (default 100, per 이랑이 interview)

    Returns
    -------
    pd.Series : VWMA values, first window-1 bars are NaN
    """

def vwma_cross(close: pd.Series, volume: pd.Series, window: int = 100) -> pd.Series:
    """VWMA cross signal.

    Returns
    -------
    pd.Series[str | None] : "golden" when close crosses above VWMA,
                            "dead" when close crosses below, None otherwise.
    Uses shift(1) to ensure causal (no lookahead).
    """
```

**신호 레지스트리 등록**: `src/signals/` 패턴과 동일하게 `@register("vwma", inputs=["close", "volume"], ...)` 데코레이터 적용도 고려하되, `src/features/` 는 bench 전용이므로 레지스트리 등록은 선택적. 전략 구현 시점 (Stage 5) 에서 필요하면 등록.

#### 3.2 `src/features/ma_projection.py`

```python
def ema_slope(close: pd.Series, span: int = 100, slope_window: int = 5) -> pd.Series:
    """EMA slope: linear regression slope of EMA over slope_window bars.

    Returns pd.Series[float]. Positive = EMA rising.
    """

def ema_curvature(close: pd.Series, span: int = 100, slope_window: int = 5) -> pd.Series:
    """Second derivative of EMA (slope of slope).

    Returns pd.Series[float]. Positive = EMA accelerating upward.
    """

def ema_projection(
    close: pd.Series, span: int = 100, horizon: int = 10, slope_window: int = 5,
) -> pd.DataFrame:
    """Forward EMA projection via linear extrapolation.

    Returns DataFrame with columns:
      - ema_proj_n: projected EMA value at t+horizon
      - eta_to_cross: estimated bars until price crosses projected EMA (inf if diverging)
      - price_to_ema_gap_at_n: projected gap between current price trajectory and EMA at t+horizon
    """
```

#### 3.3 `src/features/multi_tf.py`

```python
def multi_tf_alignment(
    close_1m: pd.Series,
    volume_1m: pd.Series,
    higher_tf: str = "1h",
    vwma_window: int = 100,
) -> pd.Series:
    """Multi-timeframe VWMA100 alignment check.

    1. Resample 1m close/volume to higher_tf OHLCV
    2. Compute VWMA(window) on higher_tf
    3. Check: higher_tf close > higher_tf VWMA (정배열)
    4. Forward-fill result back to 1m index

    Returns pd.Series[bool]. True = higher TF is in bullish alignment.

    IMPORTANT: resample uses label='right', closed='right' to avoid lookahead.
    """
```

**의존성**: `vwma.py` 의 `vwma()` 함수.

#### 3.4 `src/features/time_of_day.py`

```python
def time_gate(
    index: pd.DatetimeIndex,
    blocked_hours: list[tuple[tuple[int, int], tuple[int, int]]] | None = None,
    block_weekends: bool = True,
    timezone: str = "Asia/Seoul",
) -> pd.Series:
    """Time-of-day / day-of-week gate.

    Parameters
    ----------
    index : DatetimeIndex of bar timestamps
    blocked_hours : list of ((start_h, start_m), (end_h, end_m)) ranges.
        Default: [((10, 30), (11, 0))] = 10:30~11:00 in `timezone`.
    block_weekends : exclude Saturday/Sunday
    timezone : tz for evaluating blocked_hours (default "Asia/Seoul")

    Returns pd.Series[bool]. True = trading allowed. False = blocked.
    """
```

**Variant D `time_gate` 파라미터 확정**:

> Variant D uses `time_gate(blocked_hours=[((10, 30), (11, 0))], block_weekends=True, timezone="Asia/Seoul")`. This is a faithful-to-source choice: 영상 인터뷰 컨텍스트가 KRX 주식이고 KST 10:30-11:00 이 명시된 위험 시간대. KST 시간을 24h 크립토에 적용하는 것은 의도적 실험적 가설 (KRX 발 한국 retail flow 가 KST 시간대에 BTC/ETH 가격에 영향) 이다. UTC 기반 또는 data-driven 시간대 최적화는 본 이슈 범위 밖 (후속 이슈).

#### 3.5 `src/features/cross_sectional_rs.py`

```python
def relative_strength(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    window: int = 20,
) -> pd.Series:
    """Rolling relative strength vs benchmark (e.g. UBAI).

    RS_t = rolling_mean(asset_returns, window) - rolling_mean(benchmark_returns, window)

    Returns pd.Series[float].
    """

def rs_quartile(
    asset_returns: pd.DataFrame,
    benchmark_returns: pd.Series,
    window: int = 20,
) -> pd.DataFrame:
    """Cross-sectional RS quartile assignment.

    For each asset column, compute RS vs benchmark, then assign quartile (1=top, 4=bottom).

    Returns DataFrame with same columns, values in {1, 2, 3, 4}.
    """
```

**UBAI (업비트 알트코인 벤치마크 인덱스) 산출 확정**:
- 정의: 업비트 KRW 페어 상위 20 알트코인 (BTC, ETH 제외) 시총 가중 일별 인덱스, 매월 1일 리밸런스
- 데이터 소스: 업비트 public REST API `/v1/market/all` + `/v1/ticker` (rate limit 600/min, key 불요)
- 산출 코드 위치: `src/features/cross_sectional_rs.py` 내 `compute_ubai()` 함수
- Fallback: UBAI API 불가용 시 BTC dominance 역수로 대체
- `relative_strength()` 의 `benchmark_returns` 인자로 UBAI daily return 을 전달

#### 3.6 `src/features/poc.py`

```python
def point_of_control(
    close: pd.Series,
    volume: pd.Series,
    n_bins: int = 50,
    window: int = 100,
) -> pd.DataFrame:
    """Rolling Point of Control (volume profile).

    For each bar, compute volume histogram over [window] bars, find price bin with max volume.

    Returns DataFrame:
      - poc_price: POC price level
      - poc_distance: (close - poc_price) / close (signed, fractional)
      - poc_volume_ratio: volume at POC bin / total volume (concentration)
    """
```

#### 3.7 `src/features/orderbook_flow.py`

```python
def order_book_imbalance(bid_vol: pd.Series, ask_vol: pd.Series) -> pd.Series:
    """OBI = (bid_vol - ask_vol) / (bid_vol + ask_vol). Range [-1, 1]."""

def order_flow_imbalance(
    bid_vol: pd.Series, ask_vol: pd.Series,
    bid_vol_prev: pd.Series, ask_vol_prev: pd.Series,
) -> pd.Series:
    """OFI = delta(bid_vol) - delta(ask_vol). Cumulative order flow pressure."""

def microprice_mid_gap(
    bid_price: pd.Series, ask_price: pd.Series,
    bid_vol: pd.Series, ask_vol: pd.Series,
) -> pd.Series:
    """Microprice - midprice gap.

    microprice = (bid_price * ask_vol + ask_price * bid_vol) / (bid_vol + ask_vol)
    midprice = (bid_price + ask_price) / 2
    gap = microprice - midprice

    Positive gap = buy pressure, negative = sell pressure.
    """

def aggregate_orderbook_features(
    orderbook_1s: pd.DataFrame,
    resample_freq: str = "1min",
) -> pd.DataFrame:
    """Aggregate raw 1s orderbook data to target frequency.

    Input columns: ts, bid_price, ask_price, bid_vol, ask_vol
    Output columns: obi_mean, ofi_cumsum, microprice_gap_mean, spread_mean

    Uses label='right', closed='right' for causal aggregation.
    """
```

**데이터 의존성**: #80 paper broker 의 L2 tick 데이터. 데이터 미확보 시 variant G, H 는 synthetic/mock 데이터로 테스트만 수행하고 bench 에서 "DATA_UNAVAILABLE" 플래그로 기록.

#### 단위 테스트 (`tests/test_iranyi_features.py`)

| 테스트 | 대상 | 검증 내용 |
|--------|------|----------|
| `test_vwma_equals_sma_when_volume_constant` | vwma | volume 이 상수일 때 VWMA = SMA 수렴 확인 |
| `test_vwma_window_warmup` | vwma | 첫 window-1 bar 가 NaN |
| `test_vwma_cross_causal` | vwma_cross | `assert_no_lookahead` 통과 |
| `test_ema_slope_positive_uptrend` | ma_projection | 단조 증가 시계열에서 slope > 0 |
| `test_ema_projection_linear` | ma_projection | 완전 선형 시계열에서 projection = actual |
| `test_multi_tf_no_lookahead` | multi_tf | resample label='right' 로 미래 leak 없음 확인 |
| `test_multi_tf_alignment_bullish` | multi_tf | 상위 TF close > VWMA 시 True |
| `test_time_gate_blocks_weekend` | time_of_day | 토/일 = False |
| `test_time_gate_blocks_1030` | time_of_day | 10:30~11:00 KST = False |
| `test_rs_quartile_rank` | cross_sectional_rs | 4 종목 중 상위 1개 = quartile 1 |
| `test_poc_single_price` | poc | 모든 거래가 동일 가격 -> POC = 해당 가격, distance = 0 |
| `test_obi_range` | orderbook_flow | OBI 항상 [-1, 1] |
| `test_microprice_mid_gap_symmetric` | orderbook_flow | bid_vol = ask_vol -> gap = 0 |
| `test_all_features_causal` | 전체 | `from src.signals.lookahead_guard import assert_no_lookahead` -- 일괄 (orderbook 제외 -- 별도 입력 형식). 중복 구현 금지. |

**검증 게이트**:
- `pytest tests/test_iranyi_features.py -v` 전체 통과
- `assert_no_lookahead` 통과 (vwma, vwma_cross, ema_slope, ema_projection, multi_tf, time_gate, rs, poc)
- orderbook features 는 시간축 방향으로만 집계하므로 별도 causal test (resample label='right' 확인)

**예상 엣지 케이스**:
- volume = 0 구간 (VWMA division by zero) -> NaN 반환 + 경고
- 상위 TF 에 bar 가 1개뿐일 때 (multi_tf warmup)
- 벤치마크 데이터 없을 때 (cross_sectional_rs) -> ValueError raise
- orderbook 데이터 gap (1s 에 누락 row) -> forward-fill 후 집계

---

### Stage 4: 벤치마크 스크립트 + 실험 실행

**목적**: 사전 등록된 8 variant (A-H) 를 동일 CV split 에서 일괄 실행, DSR/PBO 산출.

**생성 파일**:
- `scripts/bench_iranyi_variants.py`

**선행 단계**: Stage 2 (validation 인프라) + Stage 3 (feature 모듈)

#### `scripts/bench_iranyi_variants.py` 구조

```python
"""Pre-registered factorial experiment: 이랑이 VWMA100 8-variant bench.

Usage: python scripts/bench_iranyi_variants.py --data-dir <path> --output-dir <path>

Variant matrix (frozen, no post-hoc additions):
  A: VWMA100 cross only (baseline)
  B: A + ema_slope > 0
  C: A + multi_tf alignment
  D: A + time_of_day gate
  E: A + cross_sectional_rs (top quartile)
  F: A + poc distance filter
  G: A + orderbook flow (OBI + OFI + microprice gap)
  H: A + B + C + D + E + F + G (full stack)

Output: JSON report with per-variant metrics + DSR + PBO.
"""

VARIANT_REGISTRY: dict[str, list[str]] = {
    "A": ["vwma_cross"],
    "B": ["vwma_cross", "ema_slope"],
    "C": ["vwma_cross", "multi_tf"],
    "D": ["vwma_cross", "time_gate"],
    "E": ["vwma_cross", "cross_sectional_rs"],
    "F": ["vwma_cross", "poc_distance"],
    "G": ["vwma_cross", "obi", "ofi", "microprice_gap"],
    "H": ["vwma_cross", "ema_slope", "multi_tf", "time_gate",
           "cross_sectional_rs", "poc_distance", "obi", "ofi", "microprice_gap"],
}
"""
**합성 의미 (Composition Semantics)**:
- Variant A: `vwma_cross` 단독 시그널 (baseline)
- Variant B-G: A 의 `vwma_cross == "golden"` AND 추가 필터 (단일 추가)
- Variant H: A 의 `vwma_cross == "golden"` AND **모든** 7 추가 필터 (AND gate, full stack)
- Zero-signal-frequency 결과는 정당한 실험적 outcome -- `n_trades=0`, `sharpe=NaN`, `monthly_hit_rate=0.0` 으로 기록
- DSR 계산: n_trades=0 인 variant 는 SR 미정의 -> DSR pool 에서 제외 (N 가 동적으로 감소)
- PBO 계산: 모든 variant 가 rank 분석에 포함되며, n_trades=0 variant 는 worst-rank 부여
"""

def run_variant(variant_id: str, features: list[str], data: pd.DataFrame,
                cv_splits: list, ...) -> dict:
    """Run single variant through all CV folds, return metrics dict.

    Returns dict with keys: variant_id, status, sharpe, sortino, mdd, calmar,
    avg_rr, turnover, n_trades, monthly_hit_rate, skew, kurtosis_excess.
    Also reports trade frequency: n_trades / n_bars (signal density).
    """

def main():
    """
    1. Load data (BTC/ETH 2020-01 ~ 2025-12, Binance 1m OHLCV)
    2. Generate CV splits: PurgedKFold(n_splits=5, embargo_frac=0.01)
    3. For each variant A-H:
       a. Apply feature filters to generate signals
       b. Backtest on each fold (train for parameter selection, val for metrics)
       c. Collect: Sharpe, Sortino, MDD, Calmar, avg_rr, turnover, n_trades, monthly_hit_rate, skew, kurtosis_excess
    4. Compute DSR: deflated_sharpe_ratio(best_sr, all_srs, n_obs, skew, kurt, n_trials=8)
    5. Compute PBO: probability_of_backtest_overfitting(returns_matrix, n_groups=16)
    6. Apply gate: DSR >= 0.95 AND PBO <= 0.2 AND OOS_MDD < 25% AND monthly_hit_rate >= 50%
    7. Output JSON + human-readable summary
    """
```

**CV split 공유 보장**: 모든 variant 가 동일한 `PurgedKFold` 인스턴스에서 생성된 동일한 `(train_idx, test_idx)` 를 사용. split 은 variant loop 바깥에서 1회만 생성.

**`monthly_hit_rate` 정의**:
- 계산: `monthly_returns = daily_returns.resample('ME').sum(); monthly_hit_rate = (monthly_returns > 0).mean()`
- 필터: 거래일 < 5 일인 부분 달은 집계에서 제외 (partial-month filter)
- 출처: `12-validation-protocol.md` §3.7 "월간 hit rate >= 50%"
- 단위: float in [0, 1]
- 게이트 임계: `>= 0.5` (5년+ 구간)

**`returns_matrix` 구성 (확정)**:
- Shape: (T, N) -- T = OOS fold 의 daily return observation 수 (PurgedKFold test split 들을 chronological 순서로 concat), N = 8 variants
- Cell 의미: variant n 의 day t OOS daily return
- 거래 없는 날: return = 0.0
- 정렬 가정: CSCV 의 contiguous-block 가정 충족을 위해 OOS fold 들은 시간순으로 concat (셔플 금지)
- T 의 수치 추정: 6년 x 0.15 (test 봉인 비율) = 약 11개월 = ~330 거래일 -- CSCV n_groups=16 이면 블록당 ~20일

**데이터 분할 (SOP 준수)**:
- 전체 기간: 2020-01 ~ 2025-12 (6년)
- Train: 2020-01 ~ 2024-02 (70%, ~50개월)
- Validation: 2024-03 ~ 2025-01 (15%, ~11개월) -- PurgedKFold CV 적용 구간
- Test: 2025-02 ~ 2025-12 (15%, ~11개월) -- 봉인, 모든 수정 완료 후 단 1회 실행

**Bench Output Schema**:

a. **Variant Registry Hash** (Pre-registration witness):
```python
import hashlib, json
registry_hash = hashlib.sha256(
    json.dumps(VARIANT_REGISTRY, sort_keys=True).encode()
).hexdigest()
output["variant_registry_sha256"] = registry_hash
```

b. **Per-variant metrics dict**: `{sharpe, sortino, mdd, calmar, avg_rr, turnover, n_trades, monthly_hit_rate, skew, kurtosis_excess}`

c. **`run_variant` DATA_UNAVAILABLE 분기**:
```python
def run_variant(variant_id, features, data, cv_splits, ...):
    if "obi" in features or "ofi" in features or "microprice_gap" in features:
        if not data_loader.has_l2_tick():
            return {"variant_id": variant_id, "status": "DATA_UNAVAILABLE",
                    "n_trades": 0, "sharpe": None, "sortino": None,
                    "mdd": None, "calmar": None, "avg_rr": None,
                    "turnover": None, "monthly_hit_rate": None,
                    "skew": None, "kurtosis_excess": None}
    # ... normal path
```

d. **`main()` DSR N 동적 조정**:
```python
n_trials_actual = sum(1 for v in results if v["status"] != "DATA_UNAVAILABLE")
dsr = deflated_sharpe_ratio(..., n_trials=n_trials_actual)
```

e. **Freeze-tag commit 권고**: 첫 커밋에 VARIANT_REGISTRY 정의만 포함 + git tag `v99-variant-freeze` 부여. 후속 커밋 (bench/feature) 은 이 freeze 태그를 reference 로 PR 설명에 명시.

**Lookahead guard**: Stage 3 테스트에서 `from src.signals.lookahead_guard import assert_no_lookahead` 를 import 하여 사용. 중복 구현 금지.

**검증 게이트**:
- 모든 8 variant 가 동일 CV split 사용 (split index 해시 비교)
- DSR/PBO 수치가 유한하고 [0, 1] 범위
- 비용 포함 (Binance taker fee 0.04% round-trip 기본)
- 결과 JSON 에 VARIANT_REGISTRY 원본 + `variant_registry_sha256` + git commit hash + 실행 timestamp 포함

**예상 엣지 케이스**:
- Variant G/H: L2 tick 데이터 미확보 시 `DATA_UNAVAILABLE` 플래그 + 해당 variant 메트릭 = null, DSR N 동적 감소
- 특정 variant 의 signal 빈도가 0 (필터가 너무 tight) -> n_trades=0, Sharpe = NaN -> DSR pool 에서 제외 (N 동적 감소) + 보고
- PurgedKFold 에서 t1 (triple-barrier label completion) 필요 -> labeling.py 로 t1 생성 후 전달

---

### Stage 5: 판정 + 조건부 전략 구현

**목적**: 게이트 통과 여부에 따라 분기. 긍정 시 `vwma_cross.py` + spec 생성, 부정 시 negative result 문서화.

**선행 단계**: Stage 4 (bench 결과)

#### 경로 A: 게이트 통과 시 (DSR >= 0.95 AND PBO <= 0.2 AND OOS MDD < 25% AND 월간 hit rate >= 50%)

**생성 파일**:
- `src/backtest/strategies/vwma_cross.py` -- AsyncStrategy 구현
- `docs/specs/strategies/vwma-cross-v1.md` -- 전략 spec (리스크 연동 섹션 필수)

**`src/backtest/strategies/vwma_cross.py` 구조**:
```python
class VwmaCross:
    """VWMA100 cross strategy -- winning variant from #99 factorial experiment.

    Implements AsyncStrategy protocol.
    Entry: 1m close crosses above VWMA100 (+ winning variant's additional filters).
    Exit: close crosses below VWMA100 OR trailing stop.
    """

    SYMBOL: ClassVar[str]  # set from winning variant config
    MIN_HISTORY: ClassVar[int] = 101  # vwma window + 1

    def __init__(self, *, variant_config: dict, vol_target_annual: float = 0.15):
        ...

    async def on_bar(self, ctx: object) -> Signal | None:
        """
        1. Check bar boundary (1m)
        2. Compute VWMA100 from ctx["market_snapshot"]["ohlcv_history"]
        3. Check cross signal
        4. Apply variant filters (ema_slope, multi_tf, time_gate, etc.)
        5. Compute size via vol_target (src/risk/sizing.py)
        6. Return Signal(action, size, reason, expected_return, confidence)
        """
```

**리스크 연동 체크리스트** (`.ai.md` "리스크 연동 (필수)" 준수):
- `daily_return_series` 산출: backtest runner 에서 trade log -> daily PnL -> daily return series
- `strategy_id = "vwma_cross"`
- `docs/specs/strategies/vwma-cross-v1.md` 에 "리스크 연동: `register_strategy_returns('vwma_cross', series)`" 기재
- 단위 테스트 1건: 수익률 -> report 생성 확인

**`docs/specs/strategies/vwma-cross-v1.md` 프론트매터**:
```yaml
---
type: strategy
id: vwma-cross-v1
name: "VWMA100 Cross (이랑이 기법 기반)"
status: backtest
instruments: [BTCUSDT, ETHUSDT]
timeframe: 1m
uses_signals: [vwma-cross]
risk_rules: [max-drawdown-5pct]
owner: siwoo
created: 2026-04-27
tags: [vwma, volume, short-term, iranyi]
---
```

#### 경로 B: 게이트 미통과 시

**수정 파일**: 없음 (전략 코드 미생성)

**문서화**:
- `02_implementation.md` 에 전체 메트릭 테이블 + "모든 variant 가 게이트 미통과" 결론 기록
- "Negative result: 이랑이 VWMA100 + 8 기법 조합 모두 DSR < 0.95 또는 PBO > 0.2. 편입 보류. 후속 이슈에서 다른 접근 (선물 short, 다른 universe) 검토 가능."

**검증 게이트**:
- 경로 A: `pytest` 전체 통과 + orchestrator 등록 테스트
- 경로 B: 문서에 8 variant 전체 메트릭 존재 + 기각 사유 명시

---

### Stage 6: 판정 리포트 + .ai.md 갱신

**목적**: 실험 결과를 정식 문서화하고, 변경된 디렉토리의 .ai.md 최신화.

**생성/수정 파일**:
- `docs/work/active/000099-iranyi-vwma-research/02_implementation.md` (신규)
- `src/features/.ai.md` (Stage 3 에서 생성, 최종 확인)
- `src/ml/validation/.ai.md` (Stage 2 에서 생성, 최종 확인)
- `src/ml/.ai.md` (scope 확장: "메타라벨링 ML 모듈" -> "AFML 기반 ML + Validation 도구체인")
- `src/backtest/strategies/.ai.md` (전략 구현 시에만 수정)

**선행 단계**: Stage 5

**`02_implementation.md` 필수 내용**:
1. Variant 메트릭 테이블 (8 rows x metrics columns)
2. DSR 값 (N=8 trial-adjusted)
3. PBO 값 (CSCV N=16)
4. 승인 게이트 통과/기각 판정
5. 승리 variant ID + 해당 feature 조합 (통과 시)
6. 기각 사유 + negative result 근거 (미통과 시)
7. 실험 무결성 증거: git commit hash, CV split hash, VARIANT_REGISTRY 원본

**검증 게이트**:
- `python scripts/check_invariants.py --strict` 전체 통과
- 모든 신규/수정 디렉토리의 `.ai.md` 최신화 완료
- `02_implementation.md` 에 8 variant 전체 메트릭 존재

---

## AC (Acceptance Criteria) <-> Stage 매핑

| AC | Stage | 검증 방법 |
|----|-------|----------|
| Research 노트 4건 | 1 | `check_invariants.py --strict` + wikilink 존재 |
| `src/ml/validation/{deflated_sharpe,pbo,cscv}.py` + `.ai.md` | 2 | 파일 존재 + pytest 통과 |
| PurgedKFold 와 인터페이스 통합 | 2, 4 | bench 에서 PurgedKFold 호출 -> DSR/PBO 산출 성공 |
| DSR closed-form 재현 | 2 | `test_psr_closed_form` 통과 |
| DSR monotonicity | 2 | `test_dsr_monotonicity` 통과 |
| PBO in [0,1] | 2 | `test_pbo_range` 통과 |
| Feature 모듈 7건 | 3 | 파일 존재 + pytest 통과 + lookahead guard 통과 |
| `bench_iranyi_variants.py` | 4 | 스크립트 실행 -> JSON 출력 + 8 variant 메트릭 |
| `tests/test_iranyi_features.py` | 3 | pytest 통과 |
| vwma_cross.py (조건부) | 5A | AsyncStrategy protocol 준수 + pytest 통과 |
| vwma-cross-v1.md (조건부) | 5A | 스키마 통과 + 리스크 연동 섹션 존재 |
| orchestrator 등록 (조건부) | 5A | `register_strategy_returns` 호출 테스트 통과 |
| 판정 리포트 02_implementation.md | 6 | 8 variant 전체 메트릭 + DSR/PBO + 판정 결론 존재 |
| Negative result 문서화 (조건부) | 5B, 6 | 기각 사유 + 메트릭 테이블 존재 |

## 위험 / 완화

| 위험 | 영향 | 확률 | 완화 |
|------|------|------|------|
| DSR 인프라 구현 복잡도가 예상 초과 | Stage 2 지연 -> 전체 지연 | 중 | PSR closed-form 먼저 구현 (단순), DSR 은 PSR + SR0 계산 추가만. CSCV 는 brute-force combination 으로 구현 (최적화 후순위). |
| L2 tick 데이터 미확보 (variant G/H) | G/H variant 실행 불가 | 고 | G/H 를 `DATA_UNAVAILABLE` 로 표시하고 나머지 6 variant (A-F) 만으로 DSR 재계산 (N=6). Negative result 에 명시. |
| Feature 의존성 순환 | multi_tf 가 vwma 에 의존 -> import 순환 | 저 | `src/features/vwma.py` 를 먼저 구현, `multi_tf.py` 에서 직접 import. 순환 없는 단방향 의존. |
| 모든 variant 가 게이트 미통과 | 전략 코드 미생성 | 중 | 정상적 결과. Negative result 을 문서화하고 이슈 종료. 프로젝트에 validation 인프라 + feature 라이브러리 만으로도 가치 있음. |
| CSCV C(16,8) = 12870 조합 연산 비용 | bench 실행 시간 초과 | 저 | N=8 variant, T=~2600 1m bars/day * 365 * 6 = ~5.7M bars. CSCV 는 variant 수준 (N=8) 에서만 조합 -> 연산량 관리 가능. 필요 시 n_groups=8 로 축소 (C(8,4)=70). |
| UBAI (업비트 알트코인 인덱스) 데이터 부재 | Variant E 실행 불가 | 중 | 해결됨: 자체 산출 채택 (업비트 상위 20 알트코인 시총 가중 인덱스, 매월 1일 리밸런스). BTC dominance 역수는 fallback. `compute_ubai()` 에서 구현. |

## Pre-Mortem (3 시나리오)

### 시나리오 1: "모든 variant 가 DSR < 0.95"

**원인 가설**: VWMA100 cross 자체가 BTC/ETH 2020-2025 구간에서 alpha 가 없음. 영상의 "월 3억 -> 13억" 은 survivor bias + 단일 사례.

**결과**: 경로 B 진행. Negative result 문서화. 전략 코드 미생성.

**사후 학습**: Validation 인프라 (DSR/PBO/CSCV) + 7개 feature 모듈은 다른 전략에 재사용 가능. 프로젝트 자산으로 남음.

### 시나리오 2: "Feature 의존성 순환 또는 import 충돌"

**원인 가설**: `src/features/` 가 새 디렉토리라 `sys.path` 에 없거나, `src/signals/` 와의 namespace 충돌.

**조기 감지**: Stage 3 첫 feature (vwma) 구현 직후 `pytest` 실행으로 import 성공 확인.

**완화**: `src/features/__init__.py` 에 명시적 import, `pyproject.toml` 또는 `sys.path` 설정 확인. 기존 `src/signals/` 패턴 (`from .registry import register`) 참조.

### 시나리오 3: "L2 tick 데이터 없어서 G/H variant 실행 불가"

**원인 가설**: #80 paper broker 가 L2 tick 을 아직 저장하지 않거나, 형식이 예상과 다름.

**결과**: G/H 를 `DATA_UNAVAILABLE` 처리. DSR 을 N=6 (A-F) 로 재계산. `02_implementation.md` 에 "G/H 는 데이터 미확보로 실행 불가, 후속 이슈에서 L2 tick 확보 후 재실험 필요" 명시.

**사후 조치**: orderbook_flow feature 자체는 mock 데이터로 단위 테스트 통과 상태로 유지 -> 데이터 확보 시 즉시 활용 가능.

## ADR (Architecture Decision Record)

**Decision**: 이랑이 8기법을 사전 등록 factorial 실험 (A-H) 으로 검증. DSR/PBO 인프라를 `src/ml/validation/` 에 신규 구현 (AFML 도구체인 통합). 단일 PR 로 atomic 머지.

**Drivers**: (1) 영상 8기법의 충실한 자동화 커버리지 필요, (2) N=8 multi-testing 보정이 실험 무결성의 핵심, (3) 사전 등록 원칙상 중간 PR 분할은 부적절.

**Alternatives Considered**: 3 PR 분할 (기각 -- 사전 등록 원칙 위반 우려 + 고아 코드 위험). 3 PR 분할 + commit-hash freeze-tag 은 사전 등록 무결성을 보존하는 valid alternative 다. 단일 PR 채택은 단순성/atomic review 를 우선한 선택이지, 분할이 기술적으로 부적절해서가 아니다.

**Why Chosen**: 단일 PR 이 실험의 atomic 성격과 가장 잘 부합. Review 부담은 Stage 별 명확한 분리로 완화.

**Consequences**: PR 크기가 15-20 파일로 대형화. Review 시 Stage 순서대로 검토 권장. CI 에서 `check_invariants.py --strict` + `pytest` 자동 실행으로 기본 품질 보장.

**Follow-ups**: (1) validation 인프라의 범용화 (다른 전략에서도 사용), (2) L2 tick 데이터 확보 시 G/H variant 재실험, (3) 선물 short 변형은 별도 이슈.

## Open Questions (해결됨/잔여)

### 해결됨

1. **UBAI 데이터 소스** (해결): 자체 산출 채택 -- 업비트 KRW 페어 상위 20 알트코인 (BTC, ETH 제외) 시총 가중 일별 인덱스, 매월 1일 리밸런스. 데이터 소스: 업비트 public REST API `/v1/market/all` + `/v1/ticker` (rate limit 600/min, key 불요). BTC dominance 역수는 fallback. 산출 코드: `src/features/cross_sectional_rs.py::compute_ubai()`.
2. **KRX 시간대 적용 여부** (해결): KST 10:30-11:00 을 그대로 적용. 이는 영상 인터뷰 원문에 충실한 선택이며, KRX 발 한국 retail flow 가 KST 시간대에 BTC/ETH 가격에 영향을 준다는 의도적 실험적 가설. UTC 기반 또는 data-driven 시간대 최적화는 본 이슈 범위 밖 (후속 이슈). Stage 3.4 Variant D 파라미터에 확정 명시됨.

### 잔여

3. **L2 tick 데이터 형식**: #80 paper broker 가 제공하는 L2 tick 의 정확한 DataFrame 스키마 (columns, frequency, timezone) 미확인. -- Variant G/H 구현 전 실측. `DATA_UNAVAILABLE` 분기로 graceful degradation 보장됨 (Bench Output Schema 참조).

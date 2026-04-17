---
type: research
id: 26-point-in-time-data
name: "Point-in-Time 데이터 설계 — 수정주가·상장폐지·생존편향 방어"
sources:
  - https://en.wikipedia.org/wiki/Survivorship_bias
  - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2595869
  - https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd
  - https://github.com/sharebook-kr/pykrx
  - https://iceberg.apache.org/docs/latest/configuration/
  - https://docs.databricks.com/en/delta/history.html
---

# Point-in-Time 데이터 설계 — 수정주가·상장폐지·생존편향 방어

> [[data-lake-schema]] 는 `asset_master.delisted_at` 컬럼만 존재하고 [[12-validation-protocol]] §2 의 체크리스트 1·2 ("survivorship bias", "look-ahead bias") 는 한 줄 언급뿐이다. 본 노트는 **구체 설계** 를 제시한다: (1) PIT snapshot 저장 전략, (2) 수정주가 계산 공식·검증, (3) 상장폐지·관리종목 처리, (4) backtest 에서 이들을 활용하는 API.

---

## 1. Point-in-Time 의 본질

**PIT** = "시점 t 의 결정을 내릴 때, 정확히 그 시점까지 알려진 정보만 본다" 라는 원칙.

실패 모드 두 가지:

1. **Look-ahead bias** — t 시점에 알 수 없었던 미래 데이터가 피처·의사결정에 섞임
2. **Survivorship bias** — 상장폐지·리네임·합병된 종목이 유니버스에서 제외되어 "살아남은 종목만" 을 대상으로 테스트 → 과대 추정

두 편향 모두 백테스트 Sharpe 를 **구조적으로 부풀린다.** Arnott et al. (2019) 는 미국 주식 장기 수익률 연구에서 survivorship bias 만으로 연 0.5~1% 수익률 과대, 데이터 스냅샷 편향과 합쳐 최대 3%까지 가는 경우를 보고.

본 프로젝트에 매핑: [[12-validation-protocol]] 의 DSR/PBO 는 "여러 시행 중 최고" 를 깎는 도구이지, 편향된 *데이터셋* 을 깎는 도구가 아님. **원천 데이터의 PIT 보존이 선행되어야 함.**

---

## 2. PIT Snapshot 저장 전략 — 3가지 아키텍처

### 2.1 Bitemporal (이상적, 복잡)

모든 레코드에 2개의 시간 축:
- `valid_time` — 현실 세계에서 사실이 유효한 기간 (event time)
- `transaction_time` — DB 에 기록된 시점 (system time)

KRX 예시: "2024-03-15 분기보고서 공시 → 거래소가 2024-05-15 에 수정 공시" → valid=2024-03-15 동일하지만 transaction 시점이 두 개.

장점: 과거 *결정 시점* 으로 완벽히 되돌림 가능.
단점: 모든 테이블에 2 타임 컬럼 + 스키마 복잡. 구현 비용 높음.

### 2.2 Append-only + `as_of` 컬럼 (본 프로젝트 권장)

모든 적재를 **append-only** 로 하고 각 행에 `ingested_at` 타임스탬프. 동일 비즈니스 키가 여러 row 로 존재하면 `ingested_at` 이 최근인 것이 현재 유효.

쿼리 시 `WHERE ingested_at <= :as_of` 로 해당 시점 스냅샷 복원. [[data-lake-schema]] 의 `ohlcv.ingested_at` 컬럼이 이미 이 전제를 깔고 있음.

**장점**: 스키마 단순 (1 타임 컬럼만 추가), Parquet append 친화적, DuckDB·Polars 쿼리로 재구성 가능.
**단점**: 정확한 `valid_time` 재현은 부정확 (공시 지연 같은 현실 복잡성 반영 못 함).

### 2.3 Delta Lake / Iceberg Time Travel

테이블 레벨 시간 여행:
```sql
-- Delta Lake
SELECT * FROM fundamentals VERSION AS OF 42;
SELECT * FROM fundamentals TIMESTAMP AS OF '2024-03-15';

-- Iceberg
SELECT * FROM fundamentals FOR VERSION AS OF 42;
SELECT * FROM fundamentals FOR TIMESTAMP AS OF '2024-03-15';
```

**장점**: 추가 컬럼 없음, 전체 테이블 PIT 복원, 롤백·포렌식 용이.
**단점**: 런타임 포맷 전환 (Parquet → Delta/Iceberg), 쓰기 성능 약간 저하.

**본 프로젝트 전략**: Phase 1~2 는 2.2 (append-only + as_of), Phase 3+ 에서 [[data-lake-schema]] 를 Delta 또는 Iceberg 로 격상 검토. 전환은 파티셔닝 규칙 유지하면 투명 (DuckDB 1.1+ Delta Lake 읽기 지원).

---

## 3. 수정주가 (Adjusted Price) 계산

### 3.1 원주가 vs 수정주가

- **원주가 (raw)**: 거래소 발표 그대로의 체결가. 액면분할·배당락 직후 가격이 이산 점프
- **수정주가 (adjusted)**: 과거 가격을 현재 기준으로 재조정해 "연속적 시계열" 처럼 보이게 함

백테스트는 **수정주가** 를 써야 모멘텀·수익률이 올바르다. 단, 시장가·호가 스프레드·체결 시뮬레이션은 **원주가** 가 맞다. 둘 다 저장해야 한다.

### 3.2 조정 계수 공식

이벤트 타입별:

| 이벤트 | 조정 계수 |
|--------|----------|
| 액면분할 N:1 | `factor = 1 / N` 을 과거 가격에 곱함 |
| 액면병합 1:N | `factor = N` 을 과거 가격에 곱함 |
| 현금배당 (배당락) | `factor = (close_prev - div) / close_prev` 를 과거 가격에 곱함 |
| 주식배당 비율 r | `factor = 1 / (1 + r)` |
| 유상증자 (권리락) | `factor = (close_prev - (close_prev - subscription_price) * r) / close_prev` 단, 복잡 — `backward_adjust` 공식은 §3.3 참조 |
| 무상증자 비율 r | `factor = 1 / (1 + r)` |

**Backward-adjusted**: 이벤트 발생일 `t*` 이후 가격은 그대로, `t < t*` 의 가격에 누적 조정. 최신일 가격이 raw 와 일치하므로 "현재 가격 기준" 시계열.

**Forward-adjusted**: 과거 그대로, 미래 가격을 조정. 과거 시점으로 가격이 고정되므로 예전 논문에서 선호. 본 프로젝트는 **backward 채택** (pykrx 기본값과 일치).

### 3.3 체계적 공식 (Backward Adjustment Chain)

```python
def backward_adjust(prices: pd.Series, events: pd.DataFrame) -> pd.Series:
    """
    prices: raw close 시계열 (index=date)
    events: ex_date, factor 컬럼을 가진 이벤트 DataFrame
    """
    factors = events.sort_values("ex_date")
    adj = prices.copy()
    # 이벤트 전날까지의 가격에 누적 factor 적용
    for _, ev in factors.iterrows():
        mask = adj.index < ev.ex_date
        adj.loc[mask] = adj.loc[mask] * ev.factor
    return adj
```

**검증 포인트**:
- `adj[last_day] == raw[last_day]` 항상 성립
- `adj[t*-1] / adj[t*] ≈ close_raw[t*-1] * factor / close_raw[t*]` 연속적 비율

### 3.4 pykrx 활용

pykrx `get_market_ohlcv_by_date(start, end, ticker, adjusted=True)` 는 위 공식을 내부 적용. 본 프로젝트는 **자체 corp_action 테이블 + 직접 계산** 권장 — pykrx 버그 발생 시 디버깅 가능, ETL 재현성 확보.

CI 에서 pykrx raw vs 자체 계산 adjusted 를 교차검증하는 단위테스트 (`tests/test_adjust_prices.py`) 필수.

---

## 4. 상장폐지·관리종목·거래정지 처리

### 4.1 상태 변화 이벤트 6종

| 이벤트 | KRX 용어 | PIT 영향 |
|--------|----------|----------|
| 신규상장 | IPO | `listed_at` 설정 — 이전 날짜 백테스트 universe 제외 |
| 관리종목 지정 | 투자 주의 | `is_caution = True` 플래그, 필터링 옵션 |
| 거래정지 | Trading Halt | `suspended_from` ~ `suspended_to` 구간 주가 N/A, 포지션 유지 로직 필요 |
| 상장폐지 | Delisting | `delisted_at` 설정 — 이후 가격 없음, 포지션은 공시일 가격으로 강제 청산 시뮬 |
| 사명 변경 / 리네임 | Rename | `isin` 은 불변, `symbol`·`name` 만 변경. 조인 키로 ISIN 사용 권장 |
| 합병 | M&A | 피합병 회사 상장폐지 + 존속회사 주식 비율 전환 (전환비율 corp_action 에 기록) |

### 4.2 Universe Filter API (제안)

백테스트에서 "시점 t 의 거래 가능 종목" 을 가져오는 표준 함수:

```python
def tradable_universe(
    as_of: date,
    *,
    min_adv_krw: float = 5e8,   # 일평균 거래대금 하한
    exclude_caution: bool = True,
    exclude_suspended: bool = True,
    sector_whitelist: list[str] | None = None,
) -> list[str]:
    """
    as_of 시점 KRX 유니버스 + 필터 적용. 본 함수의 결과는 반드시
    asset_master + corp_action + ohlcv 의 PIT snapshot 에서 계산.
    상장폐지된 종목도 delisted_at > as_of 이면 포함.
    """
```

- [[13-feature-alpha-catalog]] 의 `ADV20 >= 5e8 KRW` 필터를 이 함수가 구현
- [[19-portfolio-risk]] 의 ENB 계산에서 universe 변화 추적

### 4.3 상장폐지 종목의 "청산 가정"

백테스트에서 보유 중 상폐되면 어떻게 가정할지:

- **옵션 A**: 마지막 거래일 종가에 전량 청산 (현실적, 본 프로젝트 기본)
- **옵션 B**: 정리매매 기간 평균가 (보수적)
- **옵션 C**: 0원 처리 (완파 상황 보수 시뮬)

[[12-validation-protocol]] §4 의 "실거래 Sharpe vs 백테스트 Sharpe 괴리" 감시 연계 — 백테스트 가정이 낙관적이면 이 괴리가 벌어짐.

---

## 5. 편향 방어 체크리스트 (CI 레벨)

아래를 `tests/test_pit_invariants.py` 로 자동화 제안:

- [ ] **T1**: 과거 5년 각 월 말일 universe 크기가 KRX 공식 종목 수와 일치 (상장폐지·신규상장 반영)
- [ ] **T2**: 2010~2015 상장폐지 종목이 2026 백테스트 universe 에 나타나지 않음
- [ ] **T3**: 각 종목 `backward_adjusted` 시계열에서 이웃일 수익률 절댓값 > 15% 이면 그 날이 corp_action 이벤트 ex_date 와 일치
- [ ] **T4**: `ingested_at` 역순 쿼리로 재구성한 2020-01-01 snapshot 과 실 당시 저장본 대조 (회귀 기간)
- [ ] **T5**: pykrx adjusted=True vs 자체 계산 backward_adjusted 일일 가격 오차 ≤ 0.01%

---

## 6. [[data-lake-schema]] 확장 제안

기존 `corp_action` 테이블은 행위 기록만. PIT 운영을 위해 다음 컬럼·테이블 추가 필요:

```sql
-- asset_master 에 생명주기 컬럼 확장
ALTER TABLE asset_master ADD COLUMN isin VARCHAR;       -- 이미 있으나 NOT NULL 권고
ALTER TABLE asset_master ADD COLUMN caution_since DATE;
ALTER TABLE asset_master ADD COLUMN suspended_from DATE;
ALTER TABLE asset_master ADD COLUMN suspended_to DATE;

-- 수정주가 뷰 (물리화 권장)
CREATE TABLE ohlcv_adj AS
WITH e AS (
  SELECT symbol, ex_date,
    CASE action_type
      WHEN 'split'    THEN 1.0 / ratio
      WHEN 'merge'    THEN ratio
      WHEN 'dividend' THEN (prev_close - ratio) / prev_close
      WHEN 'rights'   THEN ...                -- 실제 코드는 §3.3 backward_adjust 재현
      ELSE 1.0
    END AS factor
  FROM corp_action
  JOIN ohlcv USING (symbol)
  ...
)
SELECT
  o.symbol, o.ts, o.freq, o.source,
  o.open * COALESCE(f_cum, 1) AS open_adj,
  o.close * COALESCE(f_cum, 1) AS close_adj,
  ...
FROM ohlcv o
LEFT JOIN cumulative_factors f ON (o.symbol, o.ts = f.as_of);
```

### 6.1 쿼리 경로 2종

- **백테스트·수익률 계산**: `ohlcv_adj` (수정주가)
- **체결 시뮬·슬리피지**: `ohlcv` (원주가)

각 SELECT 에 주석 필수: `-- PIT: as_of=... , adjusted=...`

---

## 7. Phase 별 도입 로드맵

1. **Phase 1 (MVP)**: `ohlcv.ingested_at` append-only 강제, `pykrx adjusted=True` 사용
2. **Phase 2**: 자체 `corp_action` 테이블 구축, `tests/test_adjust_prices.py` pykrx 교차검증
3. **Phase 3**: `ohlcv_adj` 물리화 테이블 + `tradable_universe()` API, T1~T5 불변식 CI 통합
4. **Phase 4+**: Delta Lake / Iceberg 전환 고려 (bitemporal 필요 시)

---

## 8. 자주 놓치는 이슈

1. **배당은 Total Return 계산에도 반영 필수** — `close_adj` 만 보면 배당 지급분이 "빠짐". [[19-portfolio-risk]] 의 수익률 분포 추정 시 `return = (close_adj + dividend_per_share) / close_adj.shift(1) - 1` 형태 사용
2. **티커 재사용** — KRX 는 상장폐지 후 동일 티커가 다른 회사에 할당될 수 있음. `isin` 으로 조인
3. **권리락 수정은 2-step** — 권리락일 당일 가격·이후 가격 모두 조정 대상. 단순 곱셈으로 안 됨
4. **시간대** — KRX 는 KST (UTC+9). 해외 소스 병합 시 [[data-lake-schema]] §5 의 `pl.Datetime("us", time_zone="UTC")` 고정 준수
5. **분할 후 거래량** — 가격을 1/N 로 줄이면 거래량도 N 배 조정해야 일관성 유지. [[data-lake-schema]] 는 volume 도 조정 필드 필요

---

## 관련 노트

- [[data-lake-schema]] — §4.1 OHLCV, §4.5 corp_action, asset_master 스키마 확장 대상
- [[12-validation-protocol]] — §2 체크리스트 1·2·5·6·9 가 본 노트의 보장 조건
- [[13-feature-alpha-catalog]] — 수정주가·생존자편향 언급을 본 노트가 상세화
- [[11-backtest-engine-selection]] — 백테스트 엔진이 본 노트의 PIT API 를 사용
- [[27-corporate-actions]] — 본 노트의 조정 계수를 실무 이벤트로 확장
- [[19-portfolio-risk]] — 팩터 회귀·공분산 추정 시 동일 PIT 원칙 적용
- [[20-position-sizing]] — Vol Targeting 의 σ 추정도 수정주가 기반 필수

---

## 출처

- Arnott, R. et al. (2019). *Alice's Adventures in Factorland*. Journal of Portfolio Management.
- Wikipedia — *Survivorship bias*. <https://en.wikipedia.org/wiki/Survivorship_bias>
- Dimson, E., Marsh, P., Staunton, M. (2002). *Triumph of the Optimists*. Princeton. (생존편향 장기연구)
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. Ch. 2·3 (Data Structures, PIT).
- KRX 시장 데이터 마켓플레이스. <https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd>
- pykrx 공식 GitHub (adjust 구현 참조). <https://github.com/sharebook-kr/pykrx>
- Apache Iceberg — *Time Travel*. <https://iceberg.apache.org/docs/latest/configuration/>
- Delta Lake — *Table history and time travel*. <https://docs.databricks.com/en/delta/history.html>
- DuckDB — *Delta Lake reader* (1.1+). <https://duckdb.org/docs/extensions/delta.html>
- Banz, R. W. (1981). *The relationship between return and market value of common stocks*. (사이즈 효과 — survivorship 맥락)

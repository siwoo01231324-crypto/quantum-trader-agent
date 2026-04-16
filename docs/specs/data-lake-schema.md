---
type: spec-architecture
id: data-lake-schema
name: "Data Lake Schema (Issue #20)"
owner: siwoo
status: draft
tags: []
---

# Data Lake Schema (Issue #20)

## 1. 목적
과거·실시간 시세, 호가, 체결, 팩터를 **단일 스키마**로 저장하여 백테스트와 라이브 트레이딩이 동일한 데이터 소스를 사용하도록 한다. Parquet + 카탈로그(DuckDB/Iceberg-lite) 조합으로 로컬 SSD와 S3 호환 객체 스토리지에서 동일하게 동작한다.

## 2. 후보 스키마 비교

| 안 | 저장 형식 | 장점 | 단점 | 결정 |
|---|---|---|---|---|
| A. Column-wise | 단일 long-format Parquet (`asset_id, ts, field, value`) | 스키마 진화 용이, factor 추가 무료 | 쿼리 시 pivot 필수 → 쿼리 비용 ↑ | 보류 |
| B. Row-wise (wide) | 한 row에 모든 필드 (`asset_id, ts, open, high, ...`) | 백테스트 친화 (zero-copy) | 새 컬럼 추가 시 스키마 마이그레이션 | 채택(OHLCV/Trade) |
| C. Hybrid | 코어 필드는 wide, 확장 팩터는 long | 둘의 장점 결합 | 조인 비용, 운영 복잡도 | 채택(Factor) |

→ **결정**: OHLCV/Trade/Orderbook = wide, Factor = long(hybrid).

## 3. 디렉터리 / 파티셔닝 규약

루트: `s3://qta-lake/` (또는 로컬 `./lake/`).

```
lake/
  ohlcv/         freq={1m,5m,1d}/year=YYYY/month=MM/symbol=XXXX/part-*.parquet
  orderbook/     year=YYYY/month=MM/day=DD/symbol=XXXX/part-*.parquet   # 일별 분할 (volume ↑)
  trade/         year=YYYY/month=MM/symbol=XXXX/part-*.parquet
  factor/        factor_set=v1/year=YYYY/month=MM/symbol=XXXX/part-*.parquet
  meta/
    asset_master.parquet
    corp_action.parquet
    calendar.parquet
```

파티션 키 선택 근거:
- `year/month`: 시계열 쿼리의 80%가 월 단위 윈도우 → 파일 prune 효과 큼.
- `symbol`: 종목별 백테스트 시 IO 최소화.
- Orderbook은 데이터량이 크므로 `day` 추가.

파일 크기 권장: **128 MB ~ 512 MB / part** (DuckDB·Polars 모두 효율 구간).

## 4. 코어 테이블 DDL (DuckDB/SQL)

### 4.1 OHLCV (wide)
```sql
CREATE TABLE ohlcv (
  symbol      VARCHAR NOT NULL,
  ts          TIMESTAMP NOT NULL,            -- UTC, exchange-tz는 meta.calendar에서 lookup
  freq        VARCHAR NOT NULL,              -- '1m','5m','1d'
  open        DOUBLE,
  high        DOUBLE,
  low         DOUBLE,
  close       DOUBLE,
  volume      DOUBLE,
  vwap        DOUBLE,
  trade_count BIGINT,
  source      VARCHAR NOT NULL,              -- 'krx','binance',...
  ingested_at TIMESTAMP NOT NULL DEFAULT now(),
  PRIMARY KEY(symbol, ts, freq, source)
);
```

### 4.2 Orderbook snapshot (wide, level 5)
```sql
CREATE TABLE orderbook_l5 (
  symbol VARCHAR NOT NULL,
  ts     TIMESTAMP NOT NULL,
  bid_px ARRAY[DOUBLE] NOT NULL,             -- length 5
  bid_sz ARRAY[DOUBLE] NOT NULL,
  ask_px ARRAY[DOUBLE] NOT NULL,
  ask_sz ARRAY[DOUBLE] NOT NULL,
  source VARCHAR NOT NULL,
  PRIMARY KEY(symbol, ts, source)
);
```

### 4.3 Trade (tick)
```sql
CREATE TABLE trade (
  symbol VARCHAR NOT NULL,
  ts     TIMESTAMP NOT NULL,
  price  DOUBLE NOT NULL,
  size   DOUBLE NOT NULL,
  side   VARCHAR,                            -- 'B'/'S'/NULL(unknown)
  trade_id VARCHAR,
  source VARCHAR NOT NULL,
  PRIMARY KEY(symbol, ts, trade_id, source)
);
```

### 4.4 Factor (long)
```sql
CREATE TABLE factor (
  symbol      VARCHAR NOT NULL,
  ts          TIMESTAMP NOT NULL,
  factor_set  VARCHAR NOT NULL,              -- 'v1','momo_v2'
  factor_name VARCHAR NOT NULL,              -- 'mom_20d','rsi_14'
  value       DOUBLE,
  PRIMARY KEY(symbol, ts, factor_set, factor_name)
);
```

### 4.5 Meta — Asset Master / Corporate Action / Calendar
```sql
CREATE TABLE asset_master (
  symbol      VARCHAR PRIMARY KEY,
  isin        VARCHAR,
  exchange    VARCHAR NOT NULL,              -- 'KRX','NASDAQ','BINANCE'
  asset_type  VARCHAR NOT NULL,              -- 'equity','etf','crypto','future'
  ccy         VARCHAR NOT NULL,
  listed_at   DATE,
  delisted_at DATE,
  name        VARCHAR
);

CREATE TABLE corp_action (
  symbol VARCHAR NOT NULL,
  ex_date DATE NOT NULL,
  action_type VARCHAR NOT NULL,              -- 'split','dividend','merger','rename','delist'
  ratio DOUBLE,                              -- split ratio or dividend amount
  meta JSON,
  PRIMARY KEY(symbol, ex_date, action_type)
);

CREATE TABLE calendar (
  exchange VARCHAR NOT NULL,
  date DATE NOT NULL,
  is_open BOOLEAN NOT NULL,
  open_ts TIMESTAMP,
  close_ts TIMESTAMP,
  PRIMARY KEY(exchange, date)
);
```

## 5. Polars 스키마 (Python)

`src/data_lake/schema.py` 참조. 핵심 규칙:
- 시각 컬럼은 `pl.Datetime("us", time_zone="UTC")` 고정.
- `symbol` / `source` 등 카디널리티 낮은 컬럼은 `pl.Categorical` (디스크는 dict-encoded).
- Float 가격은 `pl.Float64`, size는 `pl.Float64` (코인 소수점 8자리 대응).

## 6. 배포 시나리오

### 6.1 로컬 SSD (개발/단일 노드 백테스트)
- 경로: `./lake/`
- 엔진: DuckDB가 Parquet 직접 스캔 (`read_parquet('lake/ohlcv/**/*.parquet', hive_partitioning=true)`).
- 백업: `rclone sync ./lake s3:qta-lake` 일배치.

### 6.2 S3 호환 객체 스토리지 (스테이징/프로덕션)
- 경로: `s3://qta-lake/`
- 인증: IAM Role / `AWS_*` env. MinIO 호환.
- 캐시: 노드 로컬 NVMe에 최근 30일치 mirror (`s5cmd sync`).
- 카탈로그: DuckDB `httpfs` 확장 + manifest table (`meta/_manifest.parquet`)으로 파일 목록 캐싱.

### 6.3 라이브 → 레이크 적재 파이프라인
1. 수신기(broker WS) → 메모리 ring buffer.
2. 1분/1일 배치로 Parquet flush (Snappy 압축, row-group 128MB).
3. flush 완료 후 manifest 갱신, atomic rename (`*.tmp` → `*.parquet`).
4. 적재 후 `tests/test_schema.py` 의 schema validator를 CI에서 재실행.

## 7. 운영 규칙
- 컬럼 추가 → 마이너 버전(`schema_version`) 증가, 기존 파일은 default-null로 read.
- 컬럼 제거/타입 변경 → 메이저 버전, 별도 디렉터리(`ohlcv_v2/`)로 분리.
- 모든 적재는 idempotent (PK 기준 upsert via DuckDB temp table merge).

## 8. 관련 노트

- [[11-backtest-engine-selection]] — 본 스키마를 소비할 백테스트 엔진
- [[12-validation-protocol]] — point-in-time 데이터·생존편향 방어 원칙
- [[13-feature-alpha-catalog]] — `factor` 테이블에 저장될 피처
- [[execution-algorithms]] — 실거래 체결이 `trade` 테이블로 적재
- [[observability]] — 적재 파이프라인 메트릭 (ingest lag 등)
- [[19-portfolio-risk]] — Σ 추정용 수익률 시계열 소스

## 9. 출처
- DuckDB Parquet & Hive partitioning: https://duckdb.org/docs/data/parquet/overview
- Polars Schema/Datatype: https://docs.pola.rs/api/python/stable/reference/datatypes.html
- Apache Parquet 권장 row-group 크기: https://parquet.apache.org/docs/file-format/configurations/
- AWS S3 Parquet best practices: https://docs.aws.amazon.com/athena/latest/ug/columnar-storage.html
- KRX Corporate Action 가이드: https://open.krx.co.kr/contents/OPN/02/02010000/OPN02010000.jsp
- Iceberg vs Hive partitioning trade-off: https://iceberg.apache.org/docs/latest/partitioning/

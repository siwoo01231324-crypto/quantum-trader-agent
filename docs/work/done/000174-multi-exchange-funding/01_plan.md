---
type: work-done
id: 000174-multi-exchange-funding-01-plan
name: "구현 계획 — Multi-Exchange Funding Arbitrage (이슈 #174)"
issue: 174
created: 2026-05-04
assignee: worker-174
status: in-progress
---

# 구현 계획 — Multi-Exchange Funding Arbitrage (#174)

## 목표 요약
S4 funding carry (Sharpe 0.961, mhr 0.29) 의 monthly hit rate 를 0.50+ 로 끌어올리기 위해
3거래소 (Binance, OKX, Bybit) funding spread 차익거래 전략 골격 구축.

---

## 이번 사이클 (worker-174) 범위

### 완료 항목

| # | 파일 | 상태 |
|---|------|------|
| 1 | `src/data_lake/exchange_funding/__init__.py` | 완료 |
| 2 | `src/data_lake/exchange_funding/okx.py` | 완료 |
| 3 | `src/data_lake/exchange_funding/bybit.py` | 완료 |
| 4 | `tests/test_exchange_funding.py` | 완료 |
| 5 | `src/backtest/swing/multi_exchange_carry.py` | 완료 |
| 6 | `scripts/bench_funding_arbitrage.py` | 완료 |
| 7 | `scripts/fetch_funding_rates.py` — `--exchange` flag | 완료 |
| 8 | `docs/background/47-funding-rate-carry-perpetual.md` — §7 추가 | 완료 |
| 9 | `docs/work/active/000174-multi-exchange-funding/00_issue.md` | 완료 |
| 10 | `docs/work/active/000174-multi-exchange-funding/01_plan.md` | 완료 |

### 이번 사이클 범위 밖 (다음 단계)

- 5년 3거래소 funding 실제 fetch (시간 ~30-60분)
- 실제 backtest 실행 (F0-F5 Sharpe/MDD/mhr 수치)
- API key 없이 테스트만 (mock 사용)

---

## 아키텍처 결정

### 1. 공통 인터페이스 (`FundingFetcher` Protocol)
```python
fetch_funding_history(symbol, start, end) -> pd.DataFrame[ts, funding_rate]
```
- `ts`: UTC-aware DatetimeTZ
- `funding_rate`: float64
- 두 fetcher 모두 동일 인터페이스 준수 → bench script 에서 교체 가능

### 2. Pagination 전략

| 거래소 | 방식 | 한계 | Sleep |
|--------|------|------|-------|
| Binance | startTime forward | 1000건/req | 0.5s |
| OKX | cursor (after, 역방향) | 100건/req | 0.12s |
| Bybit | startTime forward | 200건/req | 0.5s |

### 3. Parquet 파티션 경로
```
lake/funding_rate/exchange={exchange}/symbol={symbol}/part-0.parquet
```
Binance 는 기존 legacy 경로(`lake/funding_rate/symbol={symbol}/`) 도 함께 유지 (backward-compat).

### 4. Strategy 함수 설계 (F0-F5)
- `VARIANT_REGISTRY` dict 로 bench script 에서 동적 dispatch
- `_UNAVAIL_SUFFIX` 패턴: 필수 컬럼 부재 시 `{name}_signal_unavailable` 반환 → bench 에서 감지
- F5 ensemble: 컬럼 부재 fetcher 는 0으로 graceful degrade

---

## 다음 단계 (다음 사이클)

1. **실제 fetch 실행**
   ```bash
   python scripts/fetch_funding_rates.py \
       --exchange binance,okx,bybit \
       --symbols BTCUSDT \
       --start 2020-09-01 --end 2025-12-31 \
       --output-dir lake/
   # OKX 용: --symbols BTC-USDT-SWAP
   ```

2. **bench 실행**
   ```bash
   python scripts/bench_funding_arbitrage.py \
       --lake-dir lake/ --symbol BTCUSDT \
       --output bench_output_funding_arb.json
   ```

3. **5 게이트 평가** — 특히 mhr ≥ 0.50 여부

4. **보고서 작성** — `02_implementation.md` (정식 승격 후)

---

## 검증 체크리스트

- [x] `pytest tests/test_exchange_funding.py` 통과
- [x] `python scripts/check_invariants.py --strict` 통과
- [x] `python scripts/fetch_funding_rates.py --help` — `--exchange` flag 노출
- [ ] 실제 fetch (다음 단계)
- [ ] bench 실행 (다음 단계)

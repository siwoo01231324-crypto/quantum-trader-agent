# feat: KIS 분봉 시세 fetcher + momo_kis_v1 전략 (KRX 메타라벨러 선행)

## 사용자 관점 목표
#85 메타라벨러를 KRX 고주파 데이터(15분봉)에 적용해 BTC 외 자산군에서도 유효성 검증이 가능하도록, KIS 분봉 시세 수집 인프라와 단일 종목 15m 모멘텀 전략을 마련한다.

## 배경
#79 에서 KIS 일봉 fetcher (`fetch_kis_daily_ohlcv`) + KOSPI200 universe + KRX calendar 를 마련했다. 그러나 메타라벨러 후속 검증을 위해서는 **분봉 데이터** 와 **단일 종목 고주파 전략** 이 필요하다. 현재 레포에는 KIS 분봉 TR `FHKST03010200` 미구현이고, KRX 모멘텀 전략도 없다. 본 이슈는 #79 패턴을 그대로 복제해 분봉 레이어를 추가하고 `momo_kis_v1` 전략을 구현하는 선행 인프라 작업이다.

## 완료 기준
- [x] `fetch_kis_intraday_ohlcv(symbol, start, end, interval)` OHLCV_SCHEMA 반환, 429 재시도 준수, KIS 분봉 제약(최근 30일) 문서화
- [x] `momo_kis_v1` AsyncStrategy 백테스트 단위 테스트 통과 (기본 symbol=005930, 15m)
- [x] `register_strategy_returns("momo_kis_v1", series)` 계약 준수
- [x] KRX 거래시간 gate + `is_krx_holiday` 2중 안전망 (harness + self-guard) 정상 동작
- [x] 단위 + 통합 테스트 20+ 건 green
- [x] `docs/work/active/<번호>/02_implementation.md` 에 일수익률 시계열 샘플 + 파라미터 기록
- [x] `docs/specs/strategies/momo-kis-v1.md` 작성, frontmatter type=strategy, `register_strategy_returns(...)` 섹션 명시

## 구현 플랜
1. **KIS 분봉 TR 연결**: `src/brokers/kis/tr_ids.py::TR_ID_INTRADAY_PRICE`, `schemas.py::KISIntradayBar`, `price_client.py::fetch_intraday_ohlcv_raw` 추가 (기존 일봉 fetcher 2-layer 구조 복제)
2. **data_lake 어댑터**: `src/data_lake/fetcher.py::fetch_kis_intraday_ohlcv` — 일자별 loop + 0.5s sleep + 기존 429 재시도 재사용
3. **전략 구현**: `src/backtest/strategies/momo_kis_v1.py` — AsyncStrategy, RSI divergence, `_is_my_bar_boundary(ts)` = 15m KST + 거래시간 gate, `is_krx_holiday` 체크
4. **스펙 + 카탈로그**: `docs/specs/strategies/momo-kis-v1.md` + `src/backtest/strategies/.ai.md` 갱신
5. **테스트**: `test_broker_kis_intraday.py`, `test_fetch_kis_intraday_ohlcv.py`, `test_momo_kis_v1.py`

## 의존성
- **#79 머지 필수** — 일봉 fetcher 2-layer 패턴, `kospi200.py`, `krx_calendar.py` 재사용
- #68 (브로커 커넥터, CLOSED) — `KISClient`, `auth.py`

## 주의사항
- KIS 분봉 API 는 **당일·최근 30일** 제약. 1년치 학습 데이터는 일자별 loop + sleep 필요.
- Paper 2 rps 제한 (기존 0.5s sleep 계승). Live 계좌 호출 금지.
- 분봉 데이터 보관은 `src/data_lake/` parquet 스키마 따르기 (source="kis").

## 후속
- **#B 이슈 (메타라벨러 × KIS 교차 검증)** 에서 본 이슈 산출물 소비

## 개발 체크리스트
- [x] 테스트 코드 포함
- [x] 해당 디렉토리 .ai.md 최신화
- [x] 불변식 위반 없음 (`python scripts/check_invariants.py --strict`)

## 작업 내역

### 2026-04-25

**현황**: 7/7 완료
**완료된 항목**:
- `fetch_kis_intraday_ohlcv` OHLCV_SCHEMA 반환 / 429 / 30일 제약 문서화
- `momo_kis_v1` AsyncStrategy 백테스트 단위 테스트 (7건 green)
- `register_strategy_returns("momo_kis_v1", series)` 계약 (통합 테스트 2건 green)
- KRX 거래시간 gate + `is_krx_holiday` 2중 안전망 (5건 bar boundary 테스트 green)
- 단위+통합 테스트 38건 green (AC5 충족)
- `02_implementation.md` 일수익률 시계열 샘플 + 파라미터 기록
- `docs/specs/strategies/momo-kis-v1.md` 스펙 노트 작성
**변경 파일**: 13개 (신규 8 + 수정 5)
**불변식**: 통과 (110 노트 검증)

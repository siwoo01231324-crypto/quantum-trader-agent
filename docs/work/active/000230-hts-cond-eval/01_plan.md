# 01_plan — HTS 검색식 3종 채택 평가

> ⚠️ **초안 상태**. AC 체크리스트만 옮긴 상태이며, 구현 시작 전 반드시 `/plan` 으로 구체적 단계·인터페이스·테스트 케이스를 채워넣어야 한다. `/remind-issue` 가 플랜 품질을 검증한다.

## AC 체크리스트 (이슈 #230 본문 그대로)

- [ ] **AC1**. 3종 검색식 evaluator 구현 — A~G 일간 + E 체결강도(KIS 시계열) + 5분대기 H(정적 VI 근접율 계산) + 단타 H(3분봉 20MA 지지, 키움 매뉴얼로 의미 확정 후) — 모두 실제 데이터로 재현, 근사·패스 금지
- [ ] **AC2**. 최근 5거래일(2026-05-08 ~ 2026-05-14, KRX 영업일 보정) KOSPI+KOSDAQ universe 백테스트 — entry: 조건 첫 충족 1분봉 종가 매수, exit: 1분봉 high≥+2% 익절 / low≤-2% 손절 (동일봉 동시 시 손절 우선) / EOD 종가 청산, 비용 0.015% 수수료 + 0.05% 슬리피지
- [ ] **AC3**. 채택 판정: win rate ≥ 50% AND 신호당 평균 P&L ≥ +0.3%(비용 후) AND 신호 수 ≥ 30 인 검색식만 채택, 결과를 `docs/research/hts-cond-eval-2026-05.draft.md` 에 출처(검색식 캡처 3장·KIS API 문서) 포함해 기록

## 개발 체크리스트

- [ ] 해당 디렉토리 `.ai.md` 최신화 (`src/screeners/hts_cond/.ai.md`, `src/backtest/eval/.ai.md` 신설)

## 구현 플랜 (이슈 본문 — `/plan` 으로 구체화 필요)

1. KIS API 클라이언트 확장 — 시간별 체결강도 + 정적 VI 발동가 fetch 메서드 추가, `scripts/fetch_kis_power_ratio.py` 신규
2. `src/screeners/hts_cond/` 신설 — `dts.py`(단타), `wait5m.py`(5분대기), `swing.py` 각 evaluator + 단위 테스트
3. universe + 1분봉 + 체결강도 5거래일 데이터 수집 (`cron_fetch_kis_daily.py --interval 1m` 활용, 누락률 <1% 검증)
4. `src/backtest/eval/hts_cond_*.py` 3개로 분봉 walk-forward 백테스트 (look-ahead bias 차단)
5. 결과 리포트 `.draft.md` 작성 → 채택 검색식은 후속 이슈로 `cs-hts-*-kr` universe-scan 전략 신설 분리, 폐기·보류는 사유와 조건 수정 후보 기록

## 구현 계획

### 0. 사전 조사 결과 (2026-05-14 확정)

#### 0.1 단타 H 조건 해석
- "20단순이평이 100%~999% 범위에서 지지" = **이격도 (종가/20MA × 100) 가 100~999%**
- 실질 의미: **3분봉 종가 ≥ 20MA**. 999% 상단은 사실상 무제한.
- 적용: 최근 10봉 (현재 봉 포함) 중 **1봉 이상** 이 만족하면 True
- 출처: 키움 공식 기술지표 가이드 (이격도 = (종가/이평)×100), qa12.htm "이평선 지지" 도움말. 단정 불가 부분: 단일 봉 터치 vs 연속 봉 필요 여부 — 단일 봉 1회 기준으로 우선 구현 후 검증

#### 0.2 정적 VI 발동가
- 산식: **전일종가 × 1.10** (시가 결정 전), 시가 결정 후엔 **직전 단일가 × 1.10**
- 상승 방향 정적 VI 근접율 산식: `(VI_price - current_price) / VI_price ≤ 0.03`
- KIS API 의 `vi_cls_code` 필드(N/S/D) 로 실시간 발동 여부 확인 가능, 발동가 직접 조회 endpoint 는 없음 (계산)

#### 0.3 체결강도 (CRITICAL)
- KIS `inquire-time-itemchartprice` 의 `tday_rltv` (당일 체결강도) 는 **output1 스냅샷에만 존재**, output2 분봉 row 에는 없음
- 백테스트 정밀 재현 제약: 분봉 t 시점의 누적 체결강도를 사후 재구성 어려움
- **결정**: 1차 백테스트는 **장 마감 시점 일간 누적 체결강도** 로 단일 필터 적용 (look-ahead bias 명시, 보수적 해석). 채택 후보만 후속 이슈에서 분봉 정밀 재현 (KIS `inquire-time-itemconclusion` tick 데이터로 누적 재구성)
- live 운영 시에는 매 분봉 진입 시점 `inquire-price` 호출로 실시간 `tday_rltv` 평가 (백테스트 ≠ live 평가 모델, spec 명시)

#### 0.4 Universe 사전 필터
- KOSPI + KOSDAQ 전 종목에서 **price filter (900~10,000원)** + **5봉 누적 거래량 ≥ 50만** 사전 적용 → fetch 대상 ~200~400 종목으로 축소
- 일봉 lake (`data/lake/krx_daily/`) 로 사전 필터 → 통과 종목만 1분봉 fetch

#### 0.5 Fetch 일정
- 대상: ~300 종목 × 5거래일 = 1500 종목-일
- KIS rate limit: 초당 20 req, 페이지당 ~30봉 (1일 1분봉 ≈ 380봉 = 13 페이지)
- 총 호출: ~20,000 req. 초당 15 req 안전 페이스 → **~22분** 소요. 야간 1회 fetch 가능

---

### 1. KIS API 클라이언트 확장 (`src/brokers/kis/rest.py`)

- [ ] `get_minute_bars(symbol, date, interval="1m")` — `inquire-time-itemchartprice` 페이지네이션 wrapper. 응답에 `tday_rltv` (output1) 동봉
- [ ] `get_static_vi_state(symbol)` — `inquire-price` 호출 → `{vi_cls_code, current_price, prev_close, vi_price_up, vi_proximity_up_pct}` 반환. `vi_price_up = prev_close * 1.10` 계산
- [ ] `get_daily_power_ratio(symbol, date)` — 장 마감 후 일간 누적 체결강도 단일값 조회 (1차 백테스트용)
- [ ] 단위 테스트 `tests/brokers/test_kis_rest_screening.py` — 알려진 종목 1건 fixture

### 2. 데이터 수집 (`scripts/fetch_kis_screening_data.py` 신규)

- [ ] 5거래일 (2026-05-08 ~ 2026-05-14) KRX 영업일 보정 (`src/universe/krx_calendar.py` 활용)
- [ ] Step A: 일봉 lake 에서 KOSPI+KOSDAQ universe 추출 → price + 5봉거래량 사전 필터
- [ ] Step B: 통과 종목 × 5거래일 1분봉 fetch → `data/lake/kis_1m_screening/dt=YYYY-MM-DD/symbol=XXXXXX.parquet`
- [ ] Step C: 동일 종목 × 5거래일 일간 체결강도 fetch → `data/lake/kis_power_ratio_daily.parquet`
- [ ] 누락률 < 1% 검증, 실패 종목 재시도 1회
- [ ] CLI: `--start 2026-05-08 --end 2026-05-14 --rate 15`

### 3. 검색식 Evaluator (`src/screeners/hts_cond/` 신설)

디렉토리 구조:
```
src/screeners/
├── __init__.py
├── .ai.md
└── hts_cond/
    ├── __init__.py
    ├── .ai.md
    ├── common.py       # A,B,C,D,E,F,G 공통 일간 조건
    ├── dts.py          # 단타 (H = 3분봉 20MA 지지)
    ├── wait5m.py       # 5분대기작전 (H = 정적 VI 근접율)
    └── swing.py        # 스윙 (G 까지)
```

- [ ] `common.py:CommonDailyConditions` — A~G 일간 조건 evaluator. dataclass 입력 (전일 종가·1봉전 종가·당일 거래량·5봉누적거래량·체결강도·5/20/60일 이평·종가)
- [ ] `dts.py:DtsCondition` — common + H 3분봉 evaluator. `evaluate(daily_inputs, three_min_bars: list[Bar]) -> bool`
- [ ] `wait5m.py:Wait5mCondition` — common + H 정적 VI 근접율 evaluator
- [ ] `swing.py:SwingCondition` — A~G 만 (H 없음, A 는 종가 9,000원 상한 + 2봉이내, B 등락률 3%+, C 거래량 50,000주+)
- [ ] 단위 테스트 `tests/screeners/test_hts_cond.py` — 각 evaluator 의 true/false case 픽스처

### 4. 백테스트 엔진 (`src/backtest/eval/` 신설)

디렉토리 구조:
```
src/backtest/eval/
├── __init__.py
├── .ai.md
├── runner.py           # 공통 백테스트 러너 (entry/exit/EOD 청산)
├── hts_cond_dts.py     # 단타 백테스트
├── hts_cond_5min.py    # 5분대기 백테스트
└── hts_cond_swing.py   # 스윙 백테스트
```

- [ ] `runner.py:BacktestRunner` — 분봉 walk-forward 엔진
  - Entry: 조건 첫 충족 1분봉 종가 매수
  - Exit: 이후 1분봉 high ≥ entry × 1.02 → +2% 익절, low ≤ entry × 0.98 → -2% 손절. **동일봉 동시 도달 시 손절 우선**
  - EOD: 15:30 종가 청산
  - 비용: 수수료 0.015% + 슬리피지 0.05% (보수적)
  - 같은 종목 1일 1회 매수 (중복 신호 무시)
  - look-ahead bias: 조건 평가 시 t-1 종가까지만 사용 (당일 분봉 진입 시점의 t-1 일봉 데이터)
- [ ] 출력: trades dataframe + summary (win rate, avg P&L, max DD, 시간대별 분포, 종목별)

### 5. 채택 판정 & 리포트

- [ ] `scripts/run_hts_cond_eval.py` — 3종 백테스트 일괄 실행 → 결과 비교
- [ ] 채택 임계값 (이슈 본문):
  - win rate ≥ 50% AND
  - 신호당 평균 P&L ≥ +0.3% (비용 후) AND
  - 신호 수 ≥ 30
- [ ] `docs/research/hts-cond-eval-2026-05.draft.md` 생성 — 검색식 캡처 3장 첨부, KIS API 출처, 백테스트 설정·결과·결론·채택/폐기 사유 명시
- [ ] 채택 시 후속 이슈 draft (`/bi chore`): "feat: cs-hts-dts-kr universe-scan 전략 신설"

### 6. 의존성 / 순서

```
1 (KIS API 확장) ─┬─→ 2 (데이터 수집) ─→ 3 (evaluator) ─→ 4 (백테스트) ─→ 5 (리포트)
                  └─→ 3 (병렬 가능)
```

### 7. 마일스톤 (3~5일 추정)

- D+0 (오늘): 데이터 수집 시작 (background), KIS API 확장 + evaluator 구현
- D+1: evaluator 단위 테스트 통과, 데이터 수집 완료
- D+2: 백테스트 엔진 + 1차 결과
- D+3: 리포트 작성 + 채택 판정 + 후속 이슈 분리

---

## 리스크 / 미해결 사항

- **체결강도 분봉 시계열 재현 불가** — 1차 백테스트는 일간 누적값으로 평가 (look-ahead bias 발생, spec 명시). 채택 후보만 후속 이슈에서 분봉 tick 누적 재구성으로 재검증
- **단타 H "지지" 단일 봉 vs 연속 봉** — 단일 봉 1회 기준으로 우선 구현. 결과 이상 시 키움 고객센터 확인
- **5거래일 표본 크기** — 신호 수 < 30 시 통계 유의성 불충분, 기간 확장 후속 이슈 (out of scope)
- **KIS API rate limit 초과** — 초당 15 req 안전 페이스 + 재시도 backoff. 운영 시간대 fetch 회피

---

## 참고

- 검색식 원본 캡처 3장 — 이슈 본문 + 사용자 첨부 (2026-05-14)
- 이슈 #227 Live Universe Scanner — 채택 시 후속 인프라 의존
- 이슈 #152 KIS 1분봉 fetch — 데이터 수집 인프라
- CLAUDE.md 불변식 #6 — LLM 주문/리스크 위임 금지, evaluator 는 룰베이스만
- KIS API: https://apiportal.koreainvestment.com/ (TR-ID `FHKST03010200` 분봉, `FHKST01010100` 현재가)
- 키움 이격도 가이드: https://www.kiwoom.com/wm/fnd/fs010/fndTechIndiGuidePop
- 키움 이평선 지지 도움말: https://download.kiwoom.com/hero4_help_new/qa12.htm

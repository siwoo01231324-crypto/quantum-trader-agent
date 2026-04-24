# feat: Signal 인터페이스 확장 — 전략 확신도·기대수익·승률을 sizer로 전달

## 목표
전략이 "이 신호의 기대수익·확신도·승률"을 sizer 로 직접 넘기도록 `Signal` 을 확장.

## 배경
- #69 에서 `src/risk/sizing.py` (Kelly·Fractional Kelly·Vol Targeting) 구현 완료.
- BTC 15m 1년 실데이터 백테스트에서 **Half-Kelly 의 win_rate 64.7% → 41.7%, 거래수 34 → 24 급락** 관찰.
- 원인: 현재 `Signal(action, size, reason)` 엔 전략의 판단(μ̂)을 담을 필드가 없음. 사이저가 "최근 60bar 평균수익률" 을 μ 로 추정하는데, 모멘텀 전략("최근 하락 후 반등 예상") 신호 의미와 방향이 불일치.
- `docs/background/20-position-sizing.md` §7.1 에서 이미 `SignalStrength(p, expected_return, sigma)` 인터페이스 제안됨 — 이를 실장.

## 범위
- `src/backtest/protocol.py` — `Signal` 에 optional 필드 추가: `expected_return`, `win_probability`, `confidence` (모두 `float | None`, 기본 None).
- `src/risk/sizing.py` 또는 전략 통합부 — Signal 에 값이 있으면 그대로 사용, 없으면 현재 fallback (과거 평균).
- `src/backtest/strategies/momo_btc_v2.py` — RSI divergence 신호 확신도를 `confidence` 로 매핑하는 결정적 규칙 정의.
- **라이브 경로**: 향후 `OrderIntent` 변환 시 동일 필드를 전파할 것. 이 이슈에서 인터페이스만 fix, 라이브 통합은 이후.
- **시그널 스펙**: `docs/specs/signals/*.md` 에 confidence 산출식을 기재 (재현성·감사 요구).
- 단위/통합 테스트.

## 불변식
- `confidence`, `win_probability`, `expected_return` 값은 **결정적 코드 계산의 결과**만 허용. LLM 출력을 이 필드에 직접 할당 금지 (CLAUDE.md 불변식 #6). 시그널 스펙에 산출식 명시.
- 기존 `Signal(action, size, reason)` 생성 경로는 그대로 작동해야 함 (optional 필드).

## 완료 기준
- [ ] `Signal` 확장 필드 + 기존 테스트 회귀 없음.
- [ ] sizer 가 `expected_return` 이 있을 때 그 값을 μ 로 사용, `win_probability` 가 있을 때 `kelly_binary` 경로 활성화.
- [ ] momo-btc-v2 에 최소 1 가지 확신도 매핑 규칙 (RSI divergence 강도 → confidence) + 실데이터 재검증 결과 JSON.
- [ ] `docs/specs/signals/rsi-divergence.md` 에 confidence 산출식 기재.
- [ ] CLAUDE.md 불변식 #6 준수 (LLM 미개입) 을 테스트 또는 문서에서 명시.

## 선행 조건
- #69 (포지션 사이징 — 머지 후).

## 관련
- #71 (알파 팩터 파이프라인) 과 독립 진행 가능. 단, `Signal` 필드명이 겹치지 않도록 PR 리뷰 시 크로스체크.
- 참고: `docs/background/20-position-sizing.md` §7.1, `docs/specs/position-sizing.md` §8, `docs/work/active/000069-position-sizing/sizing_comparison.json`.


## 작업 내역


# feat: 포지션 사이징 구현 (Kelly + vol targeting)

## 목표
진입 시 **얼마나 살지** 결정하는 포지션 사이징 모듈 구현.

## 배경
- `docs/background/20-position-sizing.md` 에 이론 조사 완료 (Kelly, fractional Kelly, vol targeting, ERC, HRP)
- 현재 코드에는 포지션 사이징 로직 없음 — 백테스트가 고정 수량

## 범위
- `src/risk/sizing.py` — Kelly, fractional Kelly (half), vol targeting
- 백테스트 엔진과 연동 (전략에서 sizing 모듈 호출)
- 전략별 sizing 방식 설정 (프론트매터 `position_sizing: half-kelly`)

## 완료 기준
- [x] `sizing.py` 에 3개 알고리즘 구현 + 단위 테스트
- [x] momo-btc-v2 백테스트에서 half-kelly sizing 적용 후 Sharpe 비교

## 선행 조건
- #67 (백테스트 엔진)

## 작업 내역

### 2026-04-24 구현
- `src/risk/sizing.py` 신규: `kelly_binary`, `kelly_continuous`, `fractional_kelly`, `vol_target`, `ewma_sigma` 순수 함수 5종. 모두 `[0, 1]` clamp, fail-closed 기본값. CLAUDE.md 불변식 #6 (LLM·네트워크 미개입) 준수.
- `src/risk/__init__.py` 에 새 심볼 export.
- `src/backtest/strategies/momo_btc_v2.py` 에 선택적 `sizing_mode: Literal["full","half-kelly","vol-target"]` 파라미터 추가. 기본값 `"full"` 로 기존 거동·테스트 완전 하위호환.
- `tests/test_risk_sizing.py` 신규: 수식 레퍼런스(p=0.55,b=1→0.10)·경계값(σ=0·negative edge)·입력 검증·pandas/numpy 호환·결정성·전략 통합 총 37 케이스 green.
- `tests/test_momo_btc_v2.py` 전체 pass (기본 `sizing_mode="full"` 덕분에 회귀 없음).
- `scripts/compare_momo_btc_v2_sizing.py` 신규: baseline·half-kelly·vol-target 3종을 동일 OHLCV 로 돌려 Sharpe·MDD·total_return·trades 를 JSON 으로 저장. 실데이터 없을 때 seed=42 synthetic 폴백.
- `docs/work/active/000069-position-sizing/sizing_comparison.json` 생성. 합성 랜덤워크에서는 엣지 부재로 half-Kelly 가 baseline 대비 개선 없음 — 실데이터 검증은 [[12-validation-protocol]] walk-forward 이슈로 분리.
- `docs/specs/position-sizing.md` 신규 (`spec-architecture`, id `position-sizing`): 설계 원칙·API·기본값 근거·불변식·전략 통합 예시·향후 작업.
- `docs/specs/.ai.md`, `docs/specs/strategies/momo-btc-v2.md`, `src/risk/.ai.md` 업데이트 + 위키링크 추가.
- 전체 테스트 421 passed + 1 skipped (기존 skip). `scripts/check_invariants.py --strict` 82 노트 통과.

### 2026-04-24 실데이터 검증 (AC #2 확장)
- `scripts/fetch_candles.py` 로 BTC 15m 1년치(2025-04-23 ~ 2026-04-23, 35,041 bars) Binance 공개 REST 로 수신 → `lake/ohlcv/freq=15m/year=.../month=.../symbol=BTCUSDT/part-0.parquet`.
- `scripts/compare_momo_btc_v2_sizing.py --data-dir lake/` 실행 → `sizing_comparison.json` 갱신. 결과: full -0.175 / half-kelly -2.212 / vol-target -0.666 (Sharpe).
- 관찰 1: 사이저 수학은 의도대로 동작 — vol-target 이 MDD 를 가장 낮게(5.097%), 거래 횟수는 baseline 과 동일(34) 유지. EWMA σ 스케일링 검증됨.
- 관찰 2: 세 모드 모두 음수 Sharpe → **momo-btc-v2 전략 자체의 엣지 부재**. 사이저 문제 아님. walk-forward / 팩터 확장은 #71 + [[12-validation-protocol]] 범위.
- 관찰 3: Half-Kelly 에서 win_rate 64.7% → 41.7% 로 급락 + 거래수 34 → 24 감소. 원인: "최근 60bar 평균수익률" 을 μ 로 쓰는 현재 방식이 모멘텀 전략 신호 의미("하락 후 반등") 와 방향 불일치. [[20-position-sizing]] §7.1 의 `SignalStrength(p, expected_return, sigma)` 인터페이스 도입이 후속 작업 필요 — **별도 이슈 생성 예정**.
- `docs/specs/position-sizing.md` §6.1 실데이터 검증 섹션 + §8 향후 작업 업데이트 완료.

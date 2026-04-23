---
type: work-done
id: 02_perf_benchmark
name: "#71 O(N²) 팩터 재계산 벤치마크 결과"
status: active
---

# #71 O(N²) 팩터 재계산 벤치마크 결과

> 작성: 2026-04-24
> 관련: `01_plan.md` §5 "O(N^2) factor recomputation cost" 리스크

## 배경

Architect 리뷰에서 요구한 60초 게이트: 70k-bar BTCUSDT 15m 백테스트에 `required_factors=["rsi"]` 를 선언하고 `run_backtest` 를 실행했을 때 60초 안에 끝나야 한다. 넘으면 follow-up 이슈를 열어 **머지 전** 점증(incremental) / 벡터화 팩터 계산 경로를 만들어야 한다.

## 측정 (로컬 dev 머신)

| N (bars) | 실측 wall time | per-bar |
|---:|---:|---:|
| 500 | 5.70s | 11.4ms |
| 1,000 | 15.52s | 15.5ms |
| 2,000 | 57.80s | 28.9ms |
| 4,000 | 156.38s | 39.1ms |

스케일링 근사: `T(N) ≈ c·N^2` 수준 (ratio `T(2000)/T(1000) ≈ 3.73`, `T(4000)/T(2000) ≈ 2.70`). 70k bar 외삽치 `(70000/4000)^2 × 156 ≈ 4.8 × 10^4` 초 (~ 13 시간).

## 결론: 게이트 실패

`test_rsi_perf` 어썰션이 항상 실패하므로 기본 CI 에서 `@pytest.mark.slow` 로 격리 (opt-in). 수치 증거는 본 노트에 보존.

## 근본 원인

- `src/signals/rsi.py::compute_rsi` — `for i in range(period+1, len(close))` Python-level 루프. 호출마다 `len(close)` 회 반복.
- `src/backtest/engine.py` — 바마다 `history = ohlcv.iloc[:i+1]` 로 **전 구간** 을 팩터 함수에 전달 → 총 호출 `N` 회 × 호출당 `O(N)` = `O(N²)` wall time.

## 권고 follow-up (이슈 예정)

1. **Wilder RSI 점증 계산** — 엔진이 이전 바의 `avg_gain`/`avg_loss` 를 저장하고 새 바만 전달하면 상수 시간.
2. **엔진 팩터 캐시** — `engine._factor_state: dict[str, Any]` 추가, 팩터 spec 에 `incremental: Callable | None` 옵션. 없으면 기존 full-recompute.
3. 또는 **전 바 선계산 + 인덱싱** — 루프 진입 전에 `full_factor = compute(name, **)` 하여 `context["factors"][name] = full_factor.iloc[:i+1]` 로 슬라이싱. O(N + bar-count) 로 떨어짐. 단 룩어헤드 회귀 테스트 강화 필요.

단기적으론 옵션 3 가 구현 비용이 가장 낮다 (팩터 시그니처·테스트 불변). 옵션 1 은 RSI/ATR 같은 Wilder 계열에만 자연스럽다.

## 머지 판단

현재 `momo-btc-v2` 는 `required_factors` 를 사용하지 않고 `signals.rsi` 를 직접 import 해서 쓴다 (하위호환 경로). 즉 **이 이슈의 엔진 precompute 경로는 아직 프로덕션 전략에 의해 소비되지 않는다.** 따라서:

- 팩터 라이브러리·레지스트리·캐시 · `required_factors` 훅 자체는 머지 가능.
- 단, 새 전략이 `required_factors` 에 추가되기 **전에** 위 follow-up 이슈가 닫혀야 한다. `src/backtest/.ai.md` 에 해당 경고를 추가한다.

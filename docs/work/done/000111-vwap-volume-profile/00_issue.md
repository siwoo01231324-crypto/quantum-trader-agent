# feat: VWAP 볼륨 프로파일 실시간 blend (특허 #84-1 차용)

## 배경

#80 의 특허 차용 4건 중 1번. `docs/background/34-patents-execution-algos.md` §2 의 💎 제안 — Goldman Sachs US8571967B1 (2026-12 만료 예정) 의 (a)+(b) 구성요소 차용.

`src/execution/vwap.py::VWAPAlgo` 가 현재 정적 `volume_profile: list[float]` 사용. 장중 실시간 거래량 피드백으로 남은 슬라이스 비율을 Bayesian 갱신.

## 의존성

- 선행: #80 머지 (현재 VWAPAlgo 인터페이스 확정)
- 권장: #105 (Phase 2) 머지 후 실거래 검증

## 범위

- `src/execution/vwap.py::VWAPAlgo._emit_next()` 및 `__init__` 수정
  - `live_volume_updater` 파라미터 추가
  - `on_market_tick(tick, realized_volume)` 시그니처 확장
  - `weights[idx:] = (역사 비율 × α) + (당일 실시간 비율 × (1-α))` 블렌딩
  - `α` = `algo_params["vwap_alpha"]` (기본 0.5)
- 특허 회피: 수식·파라미터 다르게 구현 (Bayesian 갱신, α 가중 평균)

## 완료 기준

- [ ] VWAPAlgo 의 동적 갱신 로직 구현
- [ ] `tests/test_vwap_live_update.py` — 고정 profile vs 동적 갱신 비교 백테스트, 슬리피지 감소율 측정
- [ ] KRX 동시호가/VI 발동 구간 시나리오 테스트
- [ ] `src/execution/.ai.md` 갱신

## 기대 효과

- 장중 거래량 스파이크 (VI 발동, 공시 전후) 시 남은 슬라이스 자동 재분배
- VWAP 벤치마크 대비 추적 오차 (tracking error) 감소

## 참고

- `docs/background/34-patents-execution-algos.md` §2
- [[07-market-microstructure-basics]] §4 (KRX VI/동시호가)
- #80 plan 의 특허 차용 4건 섹션
- #84 (특허 리서치)

## 연결 이슈

- 선행: #80
- 관련: #84 (특허 리서치), 특허 차용 #84-2/3/4

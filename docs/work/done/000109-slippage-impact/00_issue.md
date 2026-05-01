# feat: 슬리피지 모델 활성화 (SquareRootImpact) — MockMatchingEngine Phase 2+ 확장

## 배경

#80 Phase 1 의 `MockMatchingEngine` 은 단순화 위해 0-슬립 (즉시 100% 체결, fill_price = mid) 정책. 실제 시장과 괴리. [[execution-algorithms]] §3.1 의 **SquareRootImpact** 모델 (`I = k * sigma * sqrt(qty / ADV)`) 을 활성화하여 사실적 시뮬.

## 의존성

- 선행: #80 머지 (MockMatchingEngine 인터페이스 확정)
- 권장: #105 (Phase 2 KIS 모의계좌) — 실 체결 슬립과 비교 가능

## 범위

- `src/execution/mock_matching.py::SquareRootImpact` (`SlippageModel` Protocol 구현)
- 시그모이드 등 변형 모델도 가능 (`AlmgrenChrissImpact` 등)
- `MockMatchingEngine` 의 `slippage_model` 인자에 주입 (현재 None=0-슬립)
- 파라미터: `k` (impact constant, 기본 1.0), 변동성 sigma 추정, ADV 입력

## 완료 기준

- [ ] `SquareRootImpact` 클래스 + 단위 테스트
- [ ] `MockMatchingEngine` 통합 — slippage 적용된 fill_price 검증
- [ ] 0-슬립 vs SquareRootImpact 비교 백테스트 결과
- [ ] 결정적 시드 재현성 검증 (`seed` 파라미터)
- [ ] `src/execution/.ai.md` 갱신

## 주의사항

- 본 이슈는 옵션 활성화 — 기본값은 None (0-슬립) 유지하여 #80 회귀 영향 없음
- Phase 1 완료 후 Phase 2 환경에서 검증

## 참고

- [[execution-algorithms]] §3.1 SquareRootImpact 공식
- #80 plan ADR Follow-ups
- 기술 부채 TD-5 (0-슬립 체결은 실제 시장과 괴리)

## 연결 이슈

- 선행: #80
- 관련: #105 (Phase 2 비교 검증), 특허 차용 #84-3 (TWAP 변동성 적응)

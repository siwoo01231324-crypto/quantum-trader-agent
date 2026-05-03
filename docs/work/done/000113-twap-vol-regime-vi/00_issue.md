---
type: work-done
id: 000113-twap-vol-regime-vi-00-issue
name: "feat: TWAP 볼라틸리티 레짐 적응 + KRX VI 게이트 (특허 #84-3 차용)"
issue: 113
status: done
---

# #113 TWAP 볼라틸리티 레짐 적응 + KRX VI 게이트

## 배경

특허 #84-3 (US20210272201A1 Roman Ginis) §4 (d) 구성요소 차용:
변동성 레짐 기반 매칭 빈도 조정 — ML 없이 규칙 기반으로 단순화.

## AC

- [x] TWAPAlgo 의 volatility-adaptive 로직 구현
- [x] `src/execution/krx_handler.py` 의 VI/서킷브레이커 이벤트 → TWAP 실행 루프 연결
- [x] `tests/test_twap_volatility_adaptive.py` — VI 발동 시나리오 백테스트, 슬리피지 개선율 측정
- [x] `src/execution/.ai.md` 갱신

## 구현 범위

- `TWAPAlgo.__init__`: `volatility_weight: list[float] | None = None` 파라미터 추가
- `TWAPAlgo._maybe_emit`: vol regime 기반 슬라이스 타이밍 조정
- `KRXSingleAuctionHandler.filter`: VI 발동 시 IOC 일시 정지 (이미 WAIT 정책으로 처리됨)
- `TWAPAlgo` + `KRXSingleAuctionHandler` 통합 시나리오 테스트

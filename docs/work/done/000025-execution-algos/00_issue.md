---
type: work-done
id: 000025-execution-algos-00-issue
name: "[feat] 실행 알고리즘 (TWAP/VWAP/지정가·KRX 단일가 구간)"
status: done
---

# [feat] 실행 알고리즘 (TWAP/VWAP/지정가·KRX 단일가 구간)

## 사용자 관점 목표
단일 시장가 주문 이외에 TWAP·VWAP·지정가 분할 등 실행 전략을 선택할 수 있게 한다.

## 배경
시장 충격 최소화와 KRX 단일가 구간 대응은 자동매매의 핵심. 나이브한 시장가로는 유동성 낮은 종목에서 슬리피지 폭증.

## 완료 기준
- [ ] 실행 전략 인터페이스 `ExecutionAlgorithm` 정의
- [ ] TWAP / VWAP / 지정가 슬라이스 / 시장가 4종 구현 스텁
- [ ] KRX 단일가 매매 구간(시가·종가·서킷) 대기·재전송 로직
- [ ] 슬리피지 모델 플러그인 포인트
- [ ] 단위 테스트 (모의 체결 엔진)
- [ ] `docs/specs/execution-algorithms.md`

## 구현 플랜
1. Nautilus/LEAN 인터페이스 참조
2. KRX 시간대 특성(단일가 구간) 핸들러 별도 모듈

## 개발 체크리스트
- [ ] 테스트 코드 포함
- [ ] 해당 디렉토리 .ai.md 최신화
- [ ] 불변식 위반 없음


## 작업 내역


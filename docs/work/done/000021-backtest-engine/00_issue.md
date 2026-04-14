---
type: work-done
id: 000021-backtest-engine-00-issue
name: "[research] 백테스트 엔진 선택·비교 (Zipline/Backtrader/LEAN/Nautilus)"
status: done
---

# [research] 백테스트 엔진 선택·비교 (Zipline/Backtrader/LEAN/Nautilus)

## 목적
Zipline-reloaded / Backtrader / LEAN(로컬) / Nautilus Trader / 자체 구현 중 본 프로젝트 MVP 베이스 엔진을 확정한다.

## 배경
검증 가능한 백테스트가 없으면 전략 채택 근거가 붕괴한다. Phase 2 진입 전 필수.

## 완료 기준
- [ ] 5개 후보 엔진 비교표 (언어·이벤트드리븐 여부·라이브 호환·KRX 지원·커뮤니티·유지보수 상태)
- [ ] MVP 베이스 1개 선정 + 근거
- [ ] 선정 엔진 최소 동작 샘플 코드 (파일 1개 수준) 첨부
- [ ] `docs/background/11-backtest-engine-selection.md`

## 구현 플랜
1. 각 엔진 hello-world backtest 1회 실행
2. KRX 일봉 데이터 로딩 가능성 확인
3. 라이브 브릿지(Broker adapter) 존재 여부 평가

## 개발 체크리스트
- [ ] 해당 디렉토리 .ai.md 최신화


## 작업 내역


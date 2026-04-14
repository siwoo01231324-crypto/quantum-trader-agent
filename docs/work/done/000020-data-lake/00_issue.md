---
type: work-done
id: 000020-data-lake-00-issue
name: "[feat] 데이터 레이크 스키마 설계 (OHLCV·호가·체결·팩터)"
status: done
---

# [feat] 데이터 레이크 스키마 설계 (OHLCV·호가·체결·팩터)

## 사용자 관점 목표
과거·실시간 시세, 호가, 체결, 팩터를 통합 저장·조회할 수 있는 데이터 레이크 스키마를 확정해 백테스트·라이브를 동일 소스로 운용한다.

## 배경
데이터 레이어가 흔들리면 상위 전략·리스크·모니터링 전체가 흔들린다. Phase 2 시작 전 스키마 고정 필수.

## 완료 기준
- [ ] OHLCV / Orderbook / Trade / Factor 각 Parquet 파티셔닝 규약 (연도/월/종목코드 등)
- [ ] 메타 카탈로그 스키마 (종목 마스터·상장폐지·액면분할 등 Corporate Action)
- [ ] 로컬 SSD와 S3 호환 객체 스토리지 양쪽 배포 시나리오 문서
- [ ] 샘플 parquet + DDL 스키마 스니펫 포함
- [ ] `docs/specs/data-lake-schema.md`

## 구현 플랜
1. 후보 스키마 3안(column-wise / row-wise / hybrid) 검토
2. 용량·쿼리 성능 간단 벤치마크
3. DuckDB·Polars 기준 API 스케치

## 개발 체크리스트
- [ ] 테스트 코드 포함 (스키마 검증)
- [ ] 해당 디렉토리 .ai.md 최신화
- [ ] 불변식 위반 없음


## 작업 내역


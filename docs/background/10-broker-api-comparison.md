# 브로커 Open API 비교·선정 (KIS / 키움 / LS)

> 목적: 한국 개인이 접근 가능한 대형 브로커 Open API 3종(KIS, 키움, LS)을 실증적으로 비교하여, 본 프로젝트(저빈도 규칙기반 퀀트 자동매매)의 1차 실행 채널과 2차 fallback 채널을 확정한다.

## 1. 비교 요약표

| 항목 | KIS (한국투자증권) | 키움증권 REST API | LS증권 (구 이베스트) |
|------|----|----|----|
| 공식 포털 | apiportal.koreainvestment.com | openapi.kiwoom.com | openapi.ls-sec.co.kr |
| 프로토콜 | REST + WebSocket | REST + WebSocket (2025 신규) / 기존 OpenAPI+ (COM, Windows 전용) | REST (현재) / xingAPI (legacy COM/DLL) |
| 운영 OS | 전 OS (HTTP 기반) | REST는 전 OS / OpenAPI+는 Windows 한정 | REST는 전 OS / xingAPI는 Windows |
| 인증 방식 | App Key + App Secret → OAuth2 액세스 토큰 (24h) | App Key/Secret → 토큰 발급(REST), 공동인증서 로그인(OpenAPI+) | App Key/Secret → OAuth2 토큰 |
| 주문 API | POST 매수/매도/정정/취소, GET 잔고·체결 | REST 주문 전체 지원 (2025~) | REST 주문·계좌·체결 지원 |
| 시세 | REST 스냅샷 + WebSocket 실시간 (체결·호가·계좌통보) | REST + WebSocket 실시간 | REST + WebSocket 실시간 |
| Rate Limit | **초당 20건** (계정 단위, 모의투자 초당 2건) | 초당/분당 제한 존재, 조건검색은 시세+관심+조건 합산 초당 5회·분당 1회 | 공식 수치 문서화 상대적 모호 (TR별 제한) |
| 모의투자 | 지원 (1년 갱신, 계정당 최대 2개) | 지원 (REST/OpenAPI+ 모두) | 지원 |
| 공식 SDK | Python/Java/C# 공식 GitHub (koreainvestment/open-trading-api) | 공식 개발가이드 PDF, KOA Studio, 샘플 제공 | 공식 샘플 + eBEST.OpenApi.DevCenter |
| 3rd party 라이브러리 | python-kis(활발), pykis, mojito 등 다수 | koapy, pykiwoom, kiwoom, KiwoomRestApi.Net 등 | xingAPI wrapper, k-ebest-im, node 모듈 |
| 활성 커뮤니티 | 가장 크고 최신 이슈 다수 (GitHub stars 수백~수천) | 전통적으로 큼 / REST는 2025 신규라 초기 상태 | 중소규모, 기술 블로그 다수 |
| 수수료 | 시장 평균 수준 (유관기관 + 위탁수수료, 이벤트 혜택 다수) | 업계 최저 수준 이미지, MTS 이벤트 다수 | 퀀트 친화(저수수료) 포지셔닝, API 이용료 별도 없음 |
| 안정성 정책 | 사전 유지보수 공지, 에러코드 문서화 충실 | 2025 REST 신규 → 장애·변경 빈도 상대적 높음 | 안정적이나 공식 문서 업데이트 속도 느림 |
| 러닝 커브 | 낮음 (REST-only 가능, 공식 파이썬 샘플 풍부) | 중간~높음 (OpenAPI+ Windows 종속 + REST 이원화 혼재) | 낮음~중간 (REST 단일 전환 중) |

## 2. 평가 기준 및 가중치

- 안정성/문서 품질 (30%): 공식 문서·변경 이력·에러코드 설명 품질
- 크로스플랫폼 실행 환경 (20%): Linux/컨테이너 친화(REST-only 여부)
- 샘플·커뮤니티 성숙도 (20%): 공식/3rd party 예제, 이슈 대응 속도
- Rate limit 및 실행 한계 (15%): 저빈도 전략이라 초당 수 건 이하면 충분
- 모의투자 접근성 (10%): 실계좌 없이 E2E 회귀 가능성
- 수수료/부가 제약 (5%): 장기 운용 비용

## 3. 1차 브로커 선정: **KIS (한국투자증권)**

**근거**: KIS는 3사 중 유일하게 (a) 공식 GitHub 저장소(`koreainvestment/open-trading-api`)에서 Python/Java/C# 샘플을 1st-party로 유지보수하고 있으며, (b) REST+WebSocket이 순수 HTTP 기반이라 Linux/Docker/클라우드 배포가 자연스럽다. (c) 문서화된 rate limit(초당 20건)이 명시적이라 토큰 버킷 설계가 예측 가능하다. (d) `python-kis` 등 고품질 서드파티가 활발해 장애·스펙 변경 시 커뮤니티 시그널이 빠르다. 저빈도 규칙기반 전략(일봉·분봉 수십 종목) 요구사항은 초당 2~3건 수준이라 rate limit 여유가 충분하다. 참고: [KIS Developers](https://apiportal.koreainvestment.com/apiservice), [koreainvestment/open-trading-api](https://github.com/koreainvestment/open-trading-api), [rate limit 분석](https://tgparkk.github.io/robotrader/2025/10/09/robotrader-1-70stocks-problem.html).

## 4. 2차 Fallback: **LS증권 Open API (REST)**

**근거**: (a) REST 기반으로 OS 제약이 없어 1차와 배포 파이프라인을 공유할 수 있음, (b) 역사적으로 퀀트 친화적 수수료 정책과 안정적 체결 품질 평판, (c) 키움 REST 대비 2025 시점 운영 연수가 더 길어 스펙 변경 리스크가 작음. 키움은 OpenAPI+의 Windows/COM 의존성이 남아있고 REST 신버전의 초기 불안정 가능성이 있어 2차에서 제외. 참고: [LS증권 OPEN API](https://openapi.ls-sec.co.kr/about-openapi), [증권사별 API 비교](https://mg.jnomy.com/whatis-diff-stock-open-api).

## 5. Fallback 전환 트리거

다음 중 하나 발생 시 1차(KIS) → 2차(LS) 자동/수동 전환:

1. **지속적 API 장애**: KIS 주문 API 5xx 또는 타임아웃이 15분 윈도우에서 20% 이상 지속 (observability alert 연계)
2. **인증/토큰 이슈**: OAuth 토큰 재발급 연속 3회 실패 또는 계정 잠금
3. **스펙 breaking change**: 공식 공지 후 주문/체결 필드 호환 불가 및 SDK 미갱신 기간 >3일
4. **레이트리밋 초과 반복**: rate limit 초과 알림이 일 2회 이상 3일 연속 발생 (전략 스케일업 시)
5. **심각한 장애 공지**: KIS 공지사항의 `시스템 점검 외 장애` 경보
6. **계정 제재 리스크**: ToS 위반 경고·API 사용 중지 통보

전환은 `config/broker.yml`의 `active_broker: kis|ls` 플래그 + `BrokerAdapter` 공통 인터페이스로 구현, 포지션 전이는 먼저 신규 주문 차단 → 기존 포지션 조회 일치 확인 → 활성 브로커 스왑 순서.

## 6. 추가 고려사항

- **공통 추상화 레이어**: `OrderRouter`가 `BrokerAdapter` 인터페이스(`place_order / cancel / balance / positions / stream_ticks`)를 구현. 1·2차 모두 동일 계약 준수.
- **모의투자 회귀**: 두 브로커 모두 모의계좌 지원. CI `nightly` job에서 모의투자 단위의 E2E 회귀 필수.
- **WebSocket 이중화**: 체결통보는 WebSocket 의존도가 높으므로 재연결·하트비트·시퀀스 갭 복구 로직 공통 모듈화.
- **시간 동기화**: NTP 필수(서버 시각 오차는 주문 시퀀스·체결 비교에 직접 영향).
- **키/비밀 관리**: App Key/Secret은 secret manager + env에만 존재, 레포에 평문 금지.

## 출처

- [KIS Developers 공식 포털](https://apiportal.koreainvestment.com/apiservice)
- [koreainvestment/open-trading-api (공식 GitHub)](https://github.com/koreainvestment/open-trading-api)
- [python-kis (3rd party)](https://github.com/Soju06/python-kis)
- [KIS API rate limit 사례 분석](https://tgparkk.github.io/robotrader/2025/10/09/robotrader-1-70stocks-problem.html)
- [KIS API 호출 유량 제한 대응](https://hky035.github.io/web/kis-api-throttling/)
- [키움 REST API 포털](https://openapi.kiwoom.com/)
- [키움 OpenAPI+ 개발가이드 PDF](https://download.kiwoom.com/web/openapi/kiwoom_openapi_plus_devguide_ver_1.1.pdf)
- [키움 REST API 가이드](https://openapi.kiwoom.com/guide/index?dummyVal=0)
- [LS증권 Open API 소개](https://openapi.ls-sec.co.kr/about-openapi)
- [eBEST.OpenApi.DevCenter (LS 공식 테스트 프로젝트)](https://github.com/teranum/eBEST.OpenApi.DevCenter)
- [증권사별 Open API 차이 비교](https://mg.jnomy.com/whatis-diff-stock-open-api)

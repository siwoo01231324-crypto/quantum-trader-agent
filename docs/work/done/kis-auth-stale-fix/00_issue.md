---
type: work-done
id: 00_issue
name: "KIS auth stale token 자동 감지 + 재발급 (#127 후속, #133 운영 디버깅)"
status: active
---

# fix: KIS auth stale token 자동 감지 + 재발급

## 사용자 보고 (2026-05-06)

KIS 모의계좌 daemon `qta-live-daemon` 14시간 운영 중:
- `inquire-time-itemchartprice` 호출 1287회, 200 응답 0회
- 모두 500 응답 → retry 흡수 후 결국 fetch_failed 14건
- orchestrator on_bar 1회 (= 사실상 데이터 수집 0)
- 직접 호출 (새 토큰): 200 정상

진단 결과:
- KIS 모의서버 살아있음 (한투 모바일 앱 거래 OK)
- 분봉 endpoint 자체 작동 (직접 호출 200)
- 우리 daemon 만 1287/0 = 100% 코드 측 문제
- **disk-cached 토큰이 server-side 무효화됐는데 daemon 은 모르고 사용**
- 캐시 파일 (`.omc/state/kis_token_paper.json`) 삭제 + 재시작 → 즉시 200 떨어지기 시작

근본 원인: `KISAuth._issue_token` 은 토큰 만료(`_should_renew`)만 체크. 
서버가 토큰을 invalidate 한 경우 (IP 변경, KIS 점검, 보안 정책 등) 감지 안 됨.

## 수정

### 1. `src/brokers/kis/auth.py` — `KISAuth.invalidate()` 신규

```python
def invalidate(self) -> None:
    """Server-side 무효화된 토큰 강제 폐기. 다음 get_token() 시 새 발급.
    Disk cache 도 삭제 (다른 프로세스가 같은 stale 재사용 안 하게)."""
    self._access_token = None
    self._expires_at = None
    if self._cache_path.exists():
        self._cache_path.unlink()
```

Rate limit (`_check_rate_limit`, 1분 1회) 은 그대로 — 다음 _issue_token 이 강제.

### 2. `src/brokers/kis/rest.py` — `_request_with_retry` 자동 invalidate

5xx 또는 401 응답 받으면 첫 retry 전에 1회 invalidate + retry:

```python
invalidated = False
for attempt in range(_RETRY_MAX_ATTEMPTS):
    try:
        ...
    except requests.HTTPError as exc:
        status = ...
        body_snippet = exc.response.text[:200]  # 진단용
        if (status == 401 or 500 <= status < 600) and not invalidated:
            log.warning("KIS ... returned %d body=%r — invalidating token cache", ...)
            self._auth.invalidate()
            invalidated = True
        # 5xx 면 backoff retry, 4xx 면 raise
```

핵심:
- **한 요청 내 invalidate 1회만** (`invalidated` 플래그) — 무한 루프 방지
- **5xx 응답 body 로그** (`body_snippet`) — 다음 발생 시 KIS msg_cd 확인 가능
- 401 도 처리 (표준 token expired 응답)

## 완료 기준

- [x] `KISAuth.invalidate()` 신규 메소드 — in-memory + disk cache 모두 삭제
- [x] `KISClient._request_with_retry` 5xx/401 시 1회 invalidate + retry
- [x] 5xx 응답 body 진단 로그 추가
- [x] 단위 테스트 (`tests/brokers/kis/test_kis_auth_invalidate.py`):
  - invalidate() 4 케이스 (in-memory clear / disk delete / cache 부재 / unlink 에러 swallow)
  - retry 4 케이스 (5xx triggers / 401 triggers / once-only / 2xx no-op)
- [x] 풀 회귀 통과
- [ ] 사용자 PC 운영 검증 — git pull + docker rebuild 후 500 retry 0 또는 매우 적음, warmup_loaded 증가

## 변경 파일

| 파일 | 변경 |
|---|---|
| `src/brokers/kis/auth.py` | `invalidate()` 메소드 신규 |
| `src/brokers/kis/rest.py` | `_request_with_retry` 자동 invalidate + body 로깅 |
| `tests/brokers/kis/test_kis_auth_invalidate.py` (신규) | 8 케이스 |

## 검증

- [x] pytest tests/brokers/kis/test_kis_auth_invalidate.py — 8/8 green
- [ ] pytest 풀 회귀 — 진행 중
- [ ] check_invariants --strict

## 사용자 머지 후 검증 절차

```powershell
cd D:\project\quantum-trader-agent
git pull origin master
docker compose -f docker-compose.live.yml down
docker compose -f docker-compose.live.yml build --no-cache
docker compose -f docker-compose.live.yml up -d

Start-Sleep -Seconds 60
docker logs --since 1m qta-live-daemon 2>&1 | Select-String "warmup_loaded" | Measure-Object | Select-Object -ExpandProperty Count
docker logs --since 1m qta-live-daemon 2>&1 | Select-String "returned 500" | Measure-Object | Select-Object -ExpandProperty Count
docker logs --since 1m qta-live-daemon 2>&1 | Select-String "invalidating token cache" | Measure-Object | Select-Object -ExpandProperty Count
```

기대:
- warmup_loaded ≥ 3 (3 종목 다 받음)
- 500 retries 가 다시 발생해도 invalidate 자동 트리거 → 다음 retry 200 떨어짐
- "invalidating token cache" 로그 1번 정도 (첫 stale 감지)

## 관련

- #127 v1 (이미 머지) 의 후속
- #133 운영 (4주 KIS 모의계좌) 의 안정성 보강
- 5xx body 로그는 다음 KIS 측 이슈 발생 시 정확한 진단 (msg_cd / msg1) 제공

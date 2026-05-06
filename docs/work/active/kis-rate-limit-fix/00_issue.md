---
type: work-done
id: 00_issue
name: "KIS rate limit (EGW00201) hotfix — invalidate 스킵 + 더 긴 backoff (#212 후속)"
status: active
---

# fix: KIS rate limit (EGW00201) 시 invalidate 스킵 + 더 긴 backoff

## 사용자 보고 (2026-05-06, #212 머지 후)

#212 의 5xx auto-invalidate 가 KIS rate limit (EGW00201) 도 토큰 만료로 오인하여 invalidate 호출. 토큰 재발급은 1분 1회 제한이라 즉시 막힘 → 50초 대기 → 또 fetch fail → 또 invalidate 시도 → 무한 루프.

KIS 응답 본문 (#212 의 진단 로그가 잡아냄):
```json
{"rt_cd":"1","msg1":"초당 거래건수를 초과하였습니다","msg_cd":"EGW00201"}
```

## 근본 원인

KIS 가 **rate limit 도 HTTP 500 으로 응답**. body 의 `msg_cd: EGW00201` 으로만 구분 가능. #212 의 fix 는 status code 만 보고 invalidate 결정해서 rate limit 도 일률적으로 invalidate → token starvation.

## 수정

### `src/brokers/kis/rest.py` `_request_with_retry`

5xx body 검사 → rate limit 이면 invalidate 스킵 + 더 긴 backoff:

```python
is_rate_limit = (
    "EGW00201" in body_snippet
    or "초당" in body_snippet
    or "rate" in body_snippet.lower()
)
if (status == 401 or 500 <= status < 600) and not invalidated and not is_rate_limit:
    self._auth.invalidate()
    invalidated = True
if 500 <= status < 600 and attempt < _RETRY_MAX_ATTEMPTS - 1:
    # Rate limit 일 때 더 긴 backoff (KIS 권장 초당 1회)
    base = _RETRY_BASE_DELAY * 5 if is_rate_limit else _RETRY_BASE_DELAY
    delay = base * (2 ** attempt)
```

핵심:
- **EGW00201 / "초당" / "rate"** 키워드 매치 시 invalidate 스킵
- rate limit 의 경우 backoff 5배 (0.4s → 2s base)
- 일반 5xx (서버 에러) 는 그대로 invalidate (#212 동작 유지)

## 회귀 테스트

`tests/brokers/kis/test_kis_auth_invalidate.py` 에 3 케이스 추가:
- `test_rate_limit_egw00201_does_not_invalidate` — body 에 EGW00201 있으면 invalidate 안 함
- `test_rate_limit_korean_message_does_not_invalidate` — "초당" 만으로도 감지
- `test_real_5xx_still_invalidates` — sanity, 일반 5xx 는 여전히 invalidate

## 검증

- [x] pytest tests/brokers/kis/test_kis_auth_invalidate.py — **11/11 green** (8 → 11, 3 신규)
- [x] check_invariants --strict — 180 노트 통과
- [ ] 풀 회귀 — 진행 중

## 사용자 PC 머지 후 검증

```powershell
cd D:\project\quantum-trader-agent
git pull origin master
docker compose -f docker-compose.live.yml down
docker compose -f docker-compose.live.yml build --no-cache
docker compose -f docker-compose.live.yml up -d

# 60초 대기 후 — 200 응답 떨어지는지 확인
Start-Sleep -Seconds 60
docker logs --since 2m qta-live-daemon 2>&1 | Select-String "warmup_loaded" | Measure-Object | Select-Object -ExpandProperty Count
docker logs --since 2m qta-live-daemon 2>&1 | Select-String "invalidating token cache" | Measure-Object | Select-Object -ExpandProperty Count
docker logs --since 2m qta-live-daemon 2>&1 | Select-String "초당|EGW00201" | Measure-Object | Select-Object -ExpandProperty Count
```

기대:
- warmup_loaded ≥ 3 (3 종목 다 fetch)
- "invalidating token cache" 0 또는 매우 적음 (rate limit 은 더 이상 invalidate 안 함)
- "초당" / "EGW00201" 발생해도 daemon 정상 진행

## Refs #127 #133 #212

## 후속 (선택)

추가 개선 가능:
- feed_kis polling interval 조정 (3 종목 → 1초 분리, 현재 burst)
- rate limit 시 token 발급 1분 락 풀릴 때까지 fetch loop 자체 sleep

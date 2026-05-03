---
id: 000124-dpapi-key-store
name: "DPAPI 기반 API 키 저장소 + 키 회전 UI"
type: work-done
status: in_progress
issue: 124
---

## AC 체크리스트

- [x] DPAPI 암호화·복호화 헬퍼 (src/security/dpapi.py)
- [x] %APPDATA%/qta/secrets/ 저장 구조
- [x] 키 회전 CLI — 새 blob + 구 blob 삭제 + 즉시 연결 테스트
- [x] 평문 노출 0 검증 (디스크·환경변수·로그·예외 메시지)
- [x] 단위 테스트: 다른 사용자 SID 복호화 차단 확인 (mock 방식)
- [ ] 옵션: Argon2id 추가 암호화 레이어 (선택적)

## 구현 파일

- `src/security/__init__.py`
- `src/security/dpapi.py` — DPAPI encrypt/decrypt, Windows-only
- `src/security/key_store.py` — 저장/조회/목록/삭제 API
- `src/security/rotate_cli.py` — CLI 키 회전 도구
- `tests/security/test_key_store.py`
- `tests/security/test_dpapi.py`

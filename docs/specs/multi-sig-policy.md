---
type: spec-architecture
id: multi-sig-policy
name: "다중 서명 정책 + 키 회전 절차 (#128)"
owner: siwoo
status: draft
tags: [security, release, supply-chain]
---

# 다중 서명 정책 + 키 회전 절차

> 자동 업데이트 채널 공급망 공격 방어 (#128). 백서 §10-5, 부록 B-3 차용.

## 1. 위협 모델

| 벡터 | 영향 | 방어 |
|------|------|------|
| GitHub 계정 탈취 | 악성 release 게시 | 다중 서명 (2-of-3) |
| GitHub 인프라 침해 | 모든 release 위변조 | 외부 transparency log + reproducible build |
| CI 토큰 유출 | 빌드 산출물 변조 | OIDC + 빌드 격리 + SHA256 검증 |
| 단일 서명 키 유출 | 1 키만으로 release 조작 | 2-of-3 임계 서명 |

## 2. 키 운영

| 키 | 보관 | 사용처 |
|-----|------|--------|
| K1 (메인테이너) | 하드웨어 토큰 (YubiKey) | release tag 서명 |
| K2 (CI) | GitHub Actions OIDC 단명 토큰 | 빌드 산출물 서명 |
| K3 (백업) | 오프라인 USB + 종이 백업 | K1 분실 시 복구 |

릴리즈 1건당 K1 + K2 서명 필수. K3 는 K1 회전 시에만 사용.

## 3. 서명 검증 흐름

```
릴리즈 게시:
  artifact.exe → SHA256 manifest → minisign K1 서명 → minisign K2 서명 → GitHub Release upload

클라이언트:
  1. UpdateChecker.check_latest() — release 조회
  2. SHA256 manifest 다운로드 + K1·K2 서명 검증 (둘 다 필요)
  3. download_and_verify() — artifact SHA256 확인
  4. UpdateInstaller.install() — 백업 후 교체
```

## 4. 키 회전 절차

| 트리거 | 액션 | 다운타임 |
|--------|------|---------|
| 정기 (1년) | K1 새 키 발급 → K3 로 새 K1 서명 → 사용자 공지 → 30일 grace 후 구 K1 폐기 | 0 |
| 키 유출 의심 | 즉시 신규 K1 발급 → 패치 배포 → 구 K1 immediate 폐기 + GitHub trust 갱신 | 24h |
| K3 (백업) 회전 | 5년 주기 또는 메인테이너 변경 시 | N/A |

## 5. 재현 가능 빌드

- `Dockerfile.release` (별도 PR) — 고정 base image (디지스트 pin) + 의존성 lock + `--no-cache` 빌드
- `.github/workflows/release.yml` (별도 PR) — Docker 빌드 + 산출물 SHA256 + minisign K1/K2 서명 + GitHub Release upload
- 사용자가 동일 commit 으로 빌드 시 동일 SHA256 재생산 가능해야 함

## 6. 비상정지 통합 (#27 KillSwitch)

`UpdateChecker(is_safe_to_run=...)` 콜백으로 KillSwitch 상태 주입. tripped 시 `check_latest() → None`, `download_and_verify() → RuntimeError`. 자동 업데이트는 정상 운영 상태에서만 발동.

## 7. 출처

- minisign — https://jedisct1.github.io/minisign/
- GitHub OIDC — https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/about-security-hardening-with-openid-connect
- Reproducible Builds — https://reproducible-builds.org/
- Sigstore (alternative) — https://www.sigstore.dev/

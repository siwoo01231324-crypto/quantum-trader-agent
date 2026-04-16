---
id: protege-setup
type: onboarding
name: "Protégé 5.6 — trading.ttl 편집·round-trip 가이드"
summary: Protégé 5.6 로 trading.ttl 편집·round-trip 가이드
---

# Protégé 5.6 — trading.ttl 편집·round-trip 가이드

`docs/ontology/trading.ttl` 은 **File이 진실의 원천(SOT)** 이다.
GraphDB 는 파생 인덱스일 뿐이고, Protégé 는 GUI 편집 도구다.
이 문서는 Protégé 로 온톨로지를 편집할 때 prefix·주석이 손실되지 않도록
보존하는 방법을 단계별로 설명한다.

---

## 1. 왜 Protégé?

- `trading.ttl` 에는 30+ 클래스·프로퍼티가 있다. 텍스트 편집보다 GUI 에서
  클래스 트리·상속 관계를 시각적으로 파악하는 편이 훨씬 효율적이다.
- Turtle 파일을 직접 열고 저장하므로 GraphDB 와 독립적으로 동작한다.
  GraphDB 는 편집 완료 후 `scripts/ontology_sync.py --push-graphdb` 로 재동기화한다.
- SHACL 제약 확인·클래스 계층 탐색·프로퍼티 도메인·범위 검토가 한 화면에서 가능하다.

---

## 2. 설치 (버전 pin: 5.6.4+)

### macOS

```bash
brew install --cask protege
```

### Windows / Linux

1. [https://protege.stanford.edu/](https://protege.stanford.edu/) 에서
   **Protégé Desktop 5.6.4** 이상 zip을 내려받는다.
2. 압축을 풀고 `run.sh` (Linux) 또는 `run.bat` (Windows) 로 실행한다.

### 버전 확인

Help → About → **Protégé Desktop 5.6.4** 이상이어야 한다.
5.5.x 이하는 Turtle 저장 시 주석이 소실되는 버그가 있으므로 반드시 업그레이드한다.

---

## 3. 파일 열기

1. File → Open (또는 `Ctrl+O`)
2. 파일 선택 대화상자에서 `docs/ontology/trading.ttl` 을 연다.
3. **"Select ontology format"** 대화상자가 나타나면 **Turtle** 을 선택한다.
   (RDF/XML 이 자동 선택되는 경우 반드시 Turtle 로 변경)
4. Render 메뉴 → **"Render by label"** 켜기 (권장).
   `qta:Strategy` 대신 `Strategy` 로 표시돼 가독성이 높아진다.

---

## 4. 저장 설정 (중요 — round-trip 보존)

### 파일 포맷

- File → **Save as** → 포맷 드롭다운에서 **Turtle** 선택.
  **RDF/XML 절대 금지** — prefix 순서와 `rdfs:comment` 가 모두 손실된다.
- Preferences → File Preferences → **"Use native format if available"** 체크.

### 인코딩 · 줄바꿈

- UTF-8 고정 (Protégé 5.6 기본값 — 변경하지 말 것).
- **Windows 사용자**: 저장 후 CRLF가 섞일 수 있다.
  ```bash
  git config core.autocrlf false
  ```
  위 설정을 미리 적용해 두면 git이 CRLF를 자동 변환하지 않는다.

### prefix · 주석 보존 확인

저장 후 반드시 아래를 확인한다.

```bash
head -20 docs/ontology/trading.ttl
```

출력에서 다음 네 prefix가 모두 보여야 한다.

```
@prefix qta:  ...
@prefix inst: ...
@prefix rdfs: ...
@prefix xsd:  ...
```

모든 Class·Property 에 `rdfs:comment` 가 유지되는지도 확인한다.

```bash
grep -c 'rdfs:comment' docs/ontology/trading.ttl
```

저장 전후 카운트가 동일해야 한다.

### 불변식 검사

저장 직후 반드시 실행한다.

```bash
python scripts/check_invariants.py --strict
```

실패하면 변경을 되돌리고 원인을 찾는다.

---

## 5. 편집 워크플로우

1. Protégé 에서 원하는 편집 (새 Class 추가, rdfs:comment 수정 등)을 한다.
2. File → Save (`Ctrl+S`) — **포맷이 Turtle 인지 반드시 확인**.
3. 터미널에서 diff 를 확인한다.
   ```bash
   git diff docs/ontology/trading.ttl
   ```
4. 불변식 검사를 통과시킨다.
   ```bash
   python scripts/check_invariants.py --strict
   ```
5. A-Box 인스턴스를 재생성한다.
   ```bash
   python scripts/ontology_sync.py --write
   ```
6. 로컬 GraphDB 가 실행 중이면 재동기화한다.
   ```bash
   python scripts/ontology_sync.py --push-graphdb
   ```

---

## 6. 골든 픽스처 생성 (CI 회귀 차단용)

`tests/test_ontology_roundtrip.py` 의 3개 테스트
(`test_protege_roundtrip_isomorphic`, `test_prefixes_preserved`, `test_comments_preserved`) 는
`tests/fixtures/ontology/trading_after_protege.ttl` 골든 파일이 존재해야 pass 로 전환된다.
파일이 없는 동안은 `pytest.xfail` 로 표시되어 CI를 차단하지 않는다.

### 생성 절차

1. `docs/ontology/trading.ttl` 을 Protégé 5.6.4+ 에서 연다.
2. **아무 편집도 하지 않고** File → Save as → **Turtle** 로 저장 위치를
   `tests/fixtures/ontology/trading_after_protege.ttl` 로 지정한다.
3. 저장 후 불변식 검사를 통과시킨다.
   ```bash
   python scripts/check_invariants.py --strict
   ```
4. 골든 파일을 스테이징한다.
   ```bash
   git add tests/fixtures/ontology/trading_after_protege.ttl
   ```
5. PR에 포함시켜 커밋하면 CI 재실행 시 3개 xfail 테스트가 pass 로 전환된다.

---

## 7. 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| 저장 후 prefix 순서 뒤바뀜 | RDF/XML 으로 저장됨 | File → Save as → Turtle 선택 후 재저장 |
| `rdfs:comment` 소실 | Protégé 구버전 (<5.6) | 5.6.4 이상으로 업그레이드 |
| Blank node 이름 변경 | Protégé 정규화 동작 | `rdflib isomorphic()` 이 canonical 비교로 흡수 — 무시 가능 |
| `check_invariants.py --strict` 실패 | `trading.ttl` 이 rdflib 파싱 불가 | `git diff` 로 문제 라인 찾기 → Protégé 에서 해당 트리플 수정 |
| Windows CRLF 혼입 | `core.autocrlf=true` | `git config core.autocrlf false` 후 재저장 |
| 저장 시 "Ontology already loaded" 경고 | 동일 IRI 중복 로드 | 기존 탭 닫고 다시 File → Open |

### Blank node 주의

Protégé 재저장 시 blank node 레이블(예: `_:b0`)이 바뀔 수 있다.
`rdflib.compare.isomorphic()` 은 canonical form 비교이므로 레이블 변경은 테스트를 깨지 않는다.
단, blank node 가 *구조적으로* 추가·삭제되면 `isomorphic()` 이 실패하므로
의도치 않은 구조 변경이 없는지 `git diff` 로 확인한다.

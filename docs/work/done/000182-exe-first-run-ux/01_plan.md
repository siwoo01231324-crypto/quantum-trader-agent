# 01 Plan — #182 qta.exe 첫 실행 UX

> 작성: 2026-05-05 · 사용자 더블클릭 시 콘솔창 유지 + 자동 브라우저 + 시작 배너

## 선행 의존성 (실측 확인됨)
- PR #169 (#125 FastAPI) — MERGED ✓
- #177 (production.yaml + EXE 재빌드) — **MERGED 2026-05-05** ✓ (master b9d42e8)
  - `production.yaml`: 5 전략 등록 (`momo-btc-v2`, `momo-vol-filtered`, `meanrev-pairs`, `breakout-donchian`, `momo-kis-v1`) + 메타라벨러 entry 주석
  - `qta.spec` hiddenimports 에 `src.dashboard.*`, `uvicorn` 포함
  - `live_run.py` 에 `--dashboard-port` (default 8000) + `_start_dashboard` 통합 완료

## AC 체크리스트
- [x] `scripts/live_run.py` 인자 없이 실행 시: 도움말 출력 + `input("Press Enter to exit...")` 대기 — `_show_first_run_help` + main 분기
- [x] FastAPI 서버 시작 후 `webbrowser.open` 자동 호출 (`--no-browser` 로 비활성) — `_run_pipeline` 의 `_open_after_listen` 백그라운드 task. 포트는 `--dashboard-port` (default 8000)
- [x] 콘솔창에 시작 배너 (ASCII art QTA + 버전 + 등록된 전략 수) — `_print_startup_banner` (no-args + 정상 실행 양쪽)
- [x] Windows 단위 테스트 (subprocess.run + timeout) — `tests/scripts/test_live_run_first_ux.py` 14건

## 구현 계획

### 1. `scripts/live_run.py` 변경

**1-1. no-args 분기** (parse_args 호출 전)
```python
def _is_no_args(argv: list[str] | None) -> bool:
    """sys.argv[0] 빼고 인자 0개 (또는 --help/-h 만) 인 경우 더블클릭 패턴."""
    args = argv if argv is not None else sys.argv[1:]
    return len(args) == 0
```

main() 첫 줄:
```python
def main(argv: list[str] | None = None) -> int:
    if _is_no_args(argv):
        return _show_first_run_help()
    args = parse_args(argv)
    ...
```

`_show_first_run_help()`:
- 시작 배너 + production.yaml 등록 전략 수 출력
- argparse 의 `parser.print_help()` 호출
- "Press Enter to exit..." input 대기 (테스트는 stdin 닫힌 환경 우회)
- exit 0

**1-2. 시작 배너** (`_print_startup_banner`)
```
   ___ _____ _
  / _ \_   _/_\    quantum-trader-agent v0.1.x
 | (_) || |/ _ \   Local trading desk · http://localhost:8000
  \__\_\|_/_/ \_\  Strategies: 5 · Broker: kis-paper-shadow
```

main 의 두 진입점 (no-args, 정상 실행) 모두에서 호출.

**1-3. `--no-browser` 플래그**
```python
parser.add_argument(
    "--no-browser", action="store_true", default=False,
    help="대시보드 자동 브라우저 열기 비활성화 (default: 자동 열림)",
)
```

`_start_dashboard()` 에 `auto_open: bool` 인자 추가:
```python
async def _opener():
    await asyncio.sleep(0.5)  # 서버가 listen 시작할 시간
    try:
        webbrowser.open(f"http://localhost:{port}")
    except Exception:
        logger.warning("auto-open failed (continuing)")

if auto_open:
    asyncio.create_task(_opener(), name="qta-browser-open")
```

`_run_pipeline` 에서 `args.no_browser` 를 `_start_dashboard(auto_open=not args.no_browser)` 로 전달.

**1-4. 등록된 전략 수 카운트**
```python
def _count_strategies(yaml_path: Path) -> int:
    try:
        import yaml
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        return len(data.get("strategies") or [])
    except Exception:
        return 0
```

### 2. 신규 테스트 `tests/scripts/test_live_run_first_ux.py`

| 테스트 | 검증 |
|--------|------|
| `test_is_no_args_empty` | 빈 argv → True |
| `test_is_no_args_with_symbols` | `["--symbols","BTC"]` → False |
| `test_count_strategies_from_yaml` | 합성 yaml 파일 → 정확한 카운트 |
| `test_count_strategies_missing_file` | 파일 없음 → 0 (예외 swallow) |
| `test_show_first_run_help_returns_zero` | stdin mock → exit 0 |
| `test_show_first_run_help_contains_banner` | stdout 에 "QTA" + 전략 수 포함 |
| `test_show_first_run_help_contains_usage` | argparse usage 문자열 포함 |
| `test_no_browser_flag_parsed` | parse_args 에서 인식 |
| `test_subprocess_no_args_no_crash` | `subprocess.run([python, live_run.py], input="\n", timeout=10)` exit 0 |

### 3. .ai.md 갱신
`scripts/.ai.md` 의 `live_run.py` 라인을 "+ no-args 시 help+Press Enter, 자동 브라우저, 시작 배너 (#182)" 로 업데이트.

## Guardrails

### Must Have
- `--symbols` required=True 유지 — 정상 운영 안전성. no-args 분기는 argparse 호출 **전** 처리
- `--no-browser` 기본 False (자동 열림) — UX 의도와 일치
- 더블클릭 시 콘솔창이 즉시 닫히지 않도록 input() 대기 — 핵심 AC

### Must NOT Have
- 새 외부 의존성 (pyfiglet 등) — qta.spec hiddenimports 폭증 회피
- production.yaml 파싱 실패 시 main 함수 크래시 — 0 fallback
- 테스트 환경에서 webbrowser.open 실 호출 (mock 또는 환경변수 가드)

## 리스크
- **stdin 닫힌 환경 (CI/EXE 비대화형 실행)**: `input()` 호출 시 EOFError. 처리: `try: input(); except EOFError: pass` 로 보호.
- **webbrowser.open 동기 호출 지연**: 서버 listen 시작 전에 호출되면 브라우저가 "연결 거부" 받음. `asyncio.sleep(0.5)` 로 우회.
- **EXE PyInstaller 환경 webbrowser**: Windows 기본 브라우저가 webbrowser API 로 정상 동작 — 확인됨 (stdlib).

## 변경 영향 범위
- `scripts/live_run.py` (수정)
- `tests/scripts/test_live_run_first_ux.py` (신규)
- `scripts/.ai.md` (갱신)

---

## 작업 내역

### 2026-05-05 구현 완료
- `scripts/live_run.py` 변경:
  - 신규 헬퍼 4개 (`_is_no_args`, `_count_strategies`, `_print_startup_banner`, `_show_first_run_help`)
  - `_build_parser()` 로 argparse 정의 분리 (parse_args + 첫 실행 도움말 양쪽 재사용)
  - `--no-browser` 플래그 추가 (default False)
  - `main()` 첫 줄에서 no-args 분기 → `_show_first_run_help()` 호출 (exit 0)
  - 정상 실행 시에도 `_print_startup_banner` 표시
  - `_run_pipeline(auto_open_browser=...)` 시그니처 확장 — `webbrowser.open` 백그라운드 task (서버 listen 0.8초 후)
- 신규 테스트 `tests/scripts/test_live_run_first_ux.py` 14건:
  - `_is_no_args` 3건, `_count_strategies` 4건, `_print_startup_banner` 2건
  - `_show_first_run_help` 2건, `--no-browser` 플래그 2건, subprocess smoke 1건
- 회귀: `tests/scripts/` + `tests/test_dashboard.py` + `tests/test_dashboard_ws_timeline.py` 67/67 GREEN
- 문서: `scripts/.ai.md` 의 `live_run.py` 라인에 #182 변경 명시

### 2026-05-05 B 안 (대시보드 first 모드) 추가 통합
사용자 결정으로 인자 없이 실행 시 도움말 대신 대시보드 자동 기동:
- `_run_dashboard_only_mode(port)` 신규 — uvicorn 으로 dashboard 단독 기동 + 1초 후 `webbrowser.open` + Ctrl+C 까지 대기
- `main()` 의 no-args 분기: 환경변수 `QTA_FIRST_RUN_HELP_ONLY=true` 미지정 시 dashboard 모드 (default)
- `_show_first_run_help` 는 CI/테스트용으로 keep
- `_bundle_root()` 헬퍼 — PyInstaller frozen 시 `sys._MEIPASS`, dev 환경에서 `_REPO_ROOT`. `_show_first_run_help` 와 main banner 양쪽 적용
- 신규 테스트 3건 추가 — `TestRunDashboardOnlyMode` (함수 존재, default 호출, 환경변수 fallback)
- 총 17/17 GREEN

### EXE 빌드 + 실 검증 (PyInstaller 6.20.0 + Python 3.14)
- `pyinstaller qta.spec --clean --noconfirm` → `dist/qta.exe` 118 MB
- 더블클릭 (인자 없음) 검증:
  - 시작 배너 (QTA + 버전 + URL + 등록 전략 5)
  - uvicorn listen at 127.0.0.1:8000 (Test-NetConnection True 확인)
  - 자동 브라우저 1초 후 `http://localhost:8000` 오픈
  - Ctrl+C 또는 SIGTERM 종료
- production.yaml `_MEIPASS` 인식 확인 (초기 빌드 0 → 픽스 후 5 정상)

### 단계 (2) 통합 (사용자 결정 번복)
"더블클릭만으로 거래 시작" 까지 본 PR 에서 끝내기로 사용자 합의. 추가 작업:
- `src/dashboard/run_controller.py` 신규 — `RunController` (asyncio task 핸들 + state 머신: stopped/starting/running/stopping/error)
- `src/dashboard/app.py` 신규 endpoint 3종: `GET /api/run/status`, `POST /api/run/start`, `POST /api/run/stop`
- `_render_dashboard` Q5 카드 — [거래 시작]/[거래 정지] 버튼 + 상태 표시
- `scripts/live_run.py` 의 `_run_pipeline_attached` + `_build_pipeline_factory` 신규 — 이미 떠있는 dashboard 에 거래 task attach (재기동 X)
- `_run_dashboard_only_mode` 가 `RunController(_build_pipeline_factory(state, logger))` 주입
- 신규 테스트 `tests/test_run_controller.py` 11건 (RunController 단위 + endpoint round-trip + UI 검증)

### KIS + Binance 계좌 카드 (사용자 추가 요청)
"진짜 내 계좌 맞나" 확인이 가능하도록 두 거래소 모두 표시:
- `src/dashboard/account_info.py` 신규 — `AccountInfoProvider` (5초 TTL 캐싱)
  - `_fetch_kis()` — `HANTOO_FAKE_*` env + KISClient.get_balance() → 계좌번호(마스킹), 현금, 평가금, 보유 종목
  - `_fetch_binance()` — `BINANCE_TESTNET=true` (default) 시 `BINANCE_DEMO_API_KEY/_SECRET` 우선, false 시 mainnet 키 + BinanceFuturesClient.get_balance() → API key 마스킹, 지갑 USDT, 가용 USDT
- `GET /api/account/info` endpoint — `{kis: {...}, binance: {...}}` 반환
- Q6/Q7 카드 추가 + JS 5초 폴링
- 신규 테스트 `tests/test_account_info.py` 9건

### `.env` dotenv 자동 로드 (사용자 발견 픽스)
EXE 가 `.env` 자동 로드 안 해서 `feed=kis (or auto KRX) requires env vars` 에러. 픽스:
- `_autoload_dotenv()` 신규 — frozen 시 `sys.executable` 부모 폴더 `.env`, dev 환경 시 `_REPO_ROOT/.env`. dotenv import 실패 시 noop

### EXE 재빌드 + 실 검증
- 최종 빌드: 124 MB
- KIS 카드: ✓ 연결됨 (paper) — 계좌·잔고 표시 OK
- Binance 카드: testnet (demo 키) 연결 (사용자 .env 의 `BINANCE_DEMO_API_KEY` + `BINANCE_DEMO__SECRET_API_KEY` fallback 사용)
- [거래 시작] 버튼: 동작 확인 (KRX 영업시간 외라 시그널 0건은 정상)
- 거래 시작/정지 round-trip: status 머신 정상 작동
- 회귀: 90/90 GREEN, invariants 164 노트 통과

### 후속 이슈 권장
- universe scanner (100 종목 watch + 선별 진입) — 대시보드 카드 추가 + multi-symbol feed 보강
- `BINANCE_API_KEY` 끝의 `*` 문자가 .env 에 실제 포함되어 있어 mainnet 키 사용 시 invalid format. 사용자가 .env 직접 수정 필요 (별도 chore)

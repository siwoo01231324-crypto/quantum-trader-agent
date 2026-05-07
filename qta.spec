# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for quantum-trader-agent single-file EXE
# Entry point: scripts/live_run.py (Issue #105 Phase 2 Live Loop CLI)
#
# Build:
#   pyinstaller qta.spec
#
# Output: dist/qta.exe  (target < 200 MB)
#
# Hidden imports are required because PyInstaller static analysis misses:
#   - lightgbm: loads native .dll/.so via ctypes at runtime
#   - pandas/numpy extension modules: lazy-imported submodules
#   - asyncio/websockets protocol classes registered via __init_subclass__
#   - prometheus_client: collector registry uses importlib
#   - cryptography hazmat backends: all selected at runtime via entry_points
#   - pydantic v2: compiled core extension + validators registered lazily

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

block_cipher = None

# Native DLL/.so for libs that PyInstaller misses.
# pyarrow 는 EXE 에서 제외 — Apache Arrow C++ runtime DLL 의존성 너무 깊고
# (arrow.dll, arrow_python.dll, parquet.dll 등) PyInstaller 가 다 잡지 못함.
# sklearn.utils.fixes 가 try/except 로 pyarrow import — 없어도 sklearn 정상 동작.
# data_lake/fetcher.py 의 parquet 저장은 EXE live 매매에 필수 아님 (백테스트만).
_native_binaries = (
    collect_dynamic_libs("lightgbm")
    + collect_dynamic_libs("scipy")
    + collect_dynamic_libs("sklearn")
    + collect_dynamic_libs("numpy")
    + collect_dynamic_libs("pandas")
)
_native_datas = (
    collect_data_files("lightgbm")
)

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    ["scripts/live_run.py"],
    # `src` 를 pathex 에 포함해야 `_async_orchestrator.py` 의 `from risk import ...`
    # 등 src/ 하위 패키지가 top-level 로 import 되는 코드가 EXE 안에서 동작 (#177).
    pathex=[".", "src"],
    binaries=_native_binaries,
    datas=[
        # Default config shipped inside the EXE so users can run without a
        # separate clone.  Actual secrets (API keys) are NEVER bundled.
        ("configs", "configs"),
        # Strategy frontmatter source-of-truth — dashboard /strategies and
        # /api/strategies load these via load_strategy_catalog(). Without it
        # the strategy grid renders empty inside the EXE (#178 regression).
        ("docs/specs/strategies", "docs/specs/strategies"),
        *_native_datas,
    ],
    hiddenimports=[
        # --- asyncio / event loop ---
        "asyncio",
        "asyncio.selector_events",
        "asyncio.proactor_events",  # Windows IOCP

        # --- networking ---
        "httpx",
        "httpx._transports.default",
        "httpx._transports.asgi",
        "websockets",
        "websockets.legacy",
        "websockets.legacy.client",
        "websockets.legacy.server",
        "websockets.frames",
        "websocket",           # websocket-client
        "_websocket",

        # --- data / ML ---
        "pandas",
        "pandas._libs.tslibs.np_datetime",
        "pandas._libs.tslibs.nattype",
        "pandas._libs.tslibs.timedeltas",
        "pandas._libs.tslibs.timestamps",
        "pandas._libs.tslibs.offsets",
        "pandas._libs.interval",
        "pandas._libs.hashtable",
        "pandas._libs.lib",
        "pandas._libs.missing",
        "pandas._libs.reshape",
        "pandas._libs.skiplist",
        "pandas._libs.sparse",
        "pandas._libs.testing",
        "pandas.io.formats.style",
        "numpy",
        "numpy.core._methods",
        "numpy.lib.format",
        # pyarrow 제거 — EXE bundling 에서 DLL load failed (위 _native_binaries 주석 참조)
        "lightgbm",
        "lightgbm.basic",
        "lightgbm.sklearn",
        "lightgbm.callback",
        "scikit_learn",
        "sklearn",
        "sklearn.utils._cython_blas",
        "sklearn.neighbors._partition_nodes",
        "sklearn.tree._utils",
        "sklearn.ensemble._gradient_boosting",
        "scipy",
        "scipy.sparse",
        "scipy.sparse.csgraph._validation",
        "scipy.sparse._compressed",
        "scipy.special._ufuncs",

        # --- pydantic v2 ---
        "pydantic",
        "pydantic.v1",
        "pydantic_core",

        # --- cryptography ---
        "cryptography",
        "cryptography.hazmat.backends",
        "cryptography.hazmat.backends.openssl",
        "cryptography.hazmat.primitives",
        "cryptography.hazmat.primitives.kdf.pbkdf2",
        "cryptography.hazmat.primitives.ciphers.aead",
        "cryptography.x509",

        # --- observability ---
        "prometheus_client",
        "prometheus_client.exposition",
        "prometheus_client.metrics",
        "prometheus_client.registry",

        # --- dashboard / web (#177, #181) ---
        "fastapi",
        "starlette",
        "starlette.websockets",
        "starlette.routing",
        "uvicorn",
        "uvicorn.config",
        "uvicorn.server",
        "uvicorn.loops",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "h11",
        "wsproto",

        # --- utilities ---
        "yaml",
        "dotenv",
        "filelock",
        "requests",
        "requests.adapters",
        "pytz",

        # --- project internal (dynamic imports in loop/broker/dashboard) ---
        "src",
        "src.live",
        "src.live.loop",
        "src.live.feed",
        "src.live.feed_kis",
        "src.live.snapshot_builder",
        "src.live.wal",
        "src.brokers",
        "src.brokers.kis",
        "src.brokers.kis.async_adapter",
        "src.brokers.kis.price_client",
        "src.brokers.kis.rest",
        "src.brokers.kis.auth",
        "src.brokers.config",
        "src.dashboard",
        "src.dashboard.app",
        "src.dashboard.timeline_broker",
        "src.dashboard.timeline_events",
        "src.execution",
        "src.portfolio",
        "src.portfolio.config_loader",
        "src.portfolio._strategy_adapter",
        "src.risk",
        "src.signals",
        "src.signals.rsi",
        "src.ml",
        "src.observability",
        "src.observability.metrics",
        "src.ops",
        "src.data_lake",
        "src.backtest",
        "src.backtest.strategies",
        "src.backtest.strategies.momo_btc_v2",
        "src.backtest.strategies.momo_vol_filtered",
        "src.backtest.strategies.meanrev_pairs",
        "src.backtest.strategies.breakout_donchian",
        "src.backtest.strategies.momo_kis_v1",
        # Bare-name aliases — production.yaml uses dotted import paths like
        # `backtest.strategies.momo_btc_v2.MomoBtcV2` (no `src.` prefix), and
        # `_async_orchestrator.py` does `from risk import ...`. PyInstaller's
        # static analyser only sees the `src.X` form via hiddenimports, so we
        # must register the bare aliases too — otherwise importlib at runtime
        # raises ModuleNotFoundError (#177).
        "backtest",
        "backtest.strategies",
        "backtest.strategies.momo_btc_v2",
        "backtest.strategies.momo_vol_filtered",
        "backtest.strategies.meanrev_pairs",
        "backtest.strategies.breakout_donchian",
        "backtest.strategies.momo_kis_v1",
        "backtest.protocol",
        "portfolio",
        "portfolio.config_loader",
        "portfolio._strategy_adapter",
        "portfolio._async_orchestrator",
        "risk",
        "risk.dsl",
        "risk.sizing",
        "risk.portfolio",
        "signals",
        "signals.rsi",
        "ml",
        "ml.meta_labeler",
        "live",
        "live.types",
        "universe",
        "universe.krx_calendar",
        "execution",
        "observability",
        "observability.metrics",
        "src.features",
        "src.universe",
        "src.universe.krx_calendar",
        "src.tax",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev/test deps — not needed at runtime
        "pytest",
        "pytest_asyncio",
        "pytest_cov",
        "respx",
        "responses",
        "freezegun",
        "pandas_ta",
        # Heavy notebook stack
        "IPython",
        "ipykernel",
        "jupyter",
        "matplotlib",
        "tkinter",
        # pyarrow — Apache Arrow C++ runtime DLL load failure in EXE.
        # sklearn.utils.fixes 의 try/except 로 우아한 fallback 동작.
        # data_lake/fetcher 의 parquet 은 EXE live 매매 경로에 필수 아님.
        "pyarrow",
        # torch — PyInstaller analysis 중 torch._load_dll_libraries 가 Windows
        # access violation 으로 hang. qta runtime 은 torch 직접 사용 안 함
        # (lightgbm 만 ML). transformers/sentence-transformers/일부 dev tool 이
        # transitive 로 끌어와서 분석을 멈춰버림 (#177).
        "torch",
        "torchvision",
        "torchaudio",
        # 같은 이유로 다른 무거운 ML 프레임워크도 명시 제외 — 우리는 lightgbm 만 쓴다.
        "tensorflow",
        "tensorflow_intel",
        "tensorflow_io",
        "tensorboard",
        "jax",
        "jaxlib",
        "transformers",
        "sentence_transformers",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ---------------------------------------------------------------------------
# PYZ archive
# ---------------------------------------------------------------------------
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ---------------------------------------------------------------------------
# Single-file EXE  (onefile=True → dist/qta.exe)
# ---------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="qta",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # UPX compression to keep size < 200 MB
    upx_exclude=[
        # LightGBM native DLL must not be UPX-compressed (breaks loading)
        "lib_lightgbm.dll",
        "libgomp*.dll",
    ],
    runtime_tmpdir=None,
    console=True,       # CLI tool — keep console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    version_file=None,
)

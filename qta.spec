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

# Native DLL/.so + package data for libs that PyInstaller misses.
# pyarrow: arrow.dll/parquet.dll (DLL load failed without these)
# lightgbm: lib_lightgbm.dll
# scipy: many .pyd helpers
# sklearn: precompiled extensions
_native_binaries = (
    collect_dynamic_libs("pyarrow")
    + collect_dynamic_libs("lightgbm")
    + collect_dynamic_libs("scipy")
    + collect_dynamic_libs("sklearn")
    + collect_dynamic_libs("numpy")
    + collect_dynamic_libs("pandas")
)
_native_datas = (
    collect_data_files("pyarrow")
    + collect_data_files("lightgbm")
)

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    ["scripts/live_run.py"],
    pathex=["."],
    binaries=_native_binaries,
    datas=[
        # Default config shipped inside the EXE so users can run without a
        # separate clone.  Actual secrets (API keys) are NEVER bundled.
        ("configs", "configs"),
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
        "pyarrow",
        "pyarrow.pandas_compat",
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

        # --- utilities ---
        "yaml",
        "dotenv",
        "filelock",
        "requests",
        "requests.adapters",

        # --- project internal (dynamic imports in loop/broker) ---
        "src",
        "src.live",
        "src.live.loop",
        "src.brokers",
        "src.brokers.kis",
        "src.brokers.kis.async_adapter",
        "src.brokers.config",
        "src.execution",
        "src.portfolio",
        "src.risk",
        "src.signals",
        "src.ml",
        "src.observability",
        "src.ops",
        "src.data_lake",
        "src.backtest",
        "src.features",
        "src.universe",
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

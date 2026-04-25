"""Root-level pytest configuration.

Windows asyncio compatibility: forces SelectorEventLoopPolicy so that
websockets and httpx async tests work correctly on Windows 11 (R12 in plan).
Without this, ProactorEventLoop causes WS close hang on Windows.
"""
from __future__ import annotations

import asyncio
import sys


def pytest_configure(config):
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

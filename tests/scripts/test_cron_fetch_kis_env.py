"""env mapping tests for scripts/cron_fetch_kis_daily.py:resolve_kis_credentials (#152)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def _load_module():
    script = ROOT / "scripts" / "cron_fetch_kis_daily.py"
    spec = importlib.util.spec_from_file_location("cron_fetch_kis_daily_script", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestResolveKisCredentials:
    def test_hantoo_fake_primary_with_dash_credit_number(self):
        """HANTOO_FAKE_* + HANTOO_CREDIT_NUMBER (dash format) → split into cano/acnt_prdt_cd."""
        mod = _load_module()
        env = {
            "HANTOO_FAKE_API_KEY": "key123",
            "HANTOO_FAKE_SECRET_API_KEY": "secret456",
            "HANTOO_CREDIT_NUMBER": "12345678-01",
        }
        app_key, app_secret, cano, acnt_prdt_cd = mod.resolve_kis_credentials(env)
        assert app_key == "key123"
        assert app_secret == "secret456"
        assert cano == "12345678"
        assert acnt_prdt_cd == "01"

    def test_hantoo_fake_credit_number_takes_priority(self):
        """HANTOO_FAKE_CREDIT_NUMBER (paper trading .env layout) wins over HANTOO_CREDIT_NUMBER."""
        mod = _load_module()
        env = {
            "HANTOO_FAKE_API_KEY": "k",
            "HANTOO_FAKE_SECRET_API_KEY": "s",
            "HANTOO_FAKE_CREDIT_NUMBER": "11111111-01",
            "HANTOO_CREDIT_NUMBER": "99999999-09",
        }
        app_key, app_secret, cano, acnt_prdt_cd = mod.resolve_kis_credentials(env)
        assert cano == "11111111"
        assert acnt_prdt_cd == "01"

    def test_kis_legacy_fallback_when_hantoo_missing(self):
        """Legacy KIS_APP_KEY / KIS_APP_SECRET / KIS_CANO / KIS_ACNT_PRDT_CD when HANTOO_* absent."""
        mod = _load_module()
        env = {
            "KIS_APP_KEY": "legacy_key",
            "KIS_APP_SECRET": "legacy_secret",
            "KIS_CANO": "98765432",
            "KIS_ACNT_PRDT_CD": "02",
        }
        app_key, app_secret, cano, acnt_prdt_cd = mod.resolve_kis_credentials(env)
        assert app_key == "legacy_key"
        assert app_secret == "legacy_secret"
        assert cano == "98765432"
        assert acnt_prdt_cd == "02"

    def test_hantoo_takes_priority_over_kis_legacy(self):
        """When both sets are present, HANTOO_* wins."""
        mod = _load_module()
        env = {
            "HANTOO_FAKE_API_KEY": "primary",
            "HANTOO_FAKE_SECRET_API_KEY": "primary_secret",
            "HANTOO_CREDIT_NUMBER": "11111111-01",
            "KIS_APP_KEY": "fallback",
            "KIS_APP_SECRET": "fallback_secret",
            "KIS_CANO": "22222222",
            "KIS_ACNT_PRDT_CD": "02",
        }
        app_key, app_secret, cano, acnt_prdt_cd = mod.resolve_kis_credentials(env)
        assert app_key == "primary"
        assert app_secret == "primary_secret"
        assert cano == "11111111"
        assert acnt_prdt_cd == "01"

    def test_empty_env_returns_empty_strings(self):
        """Missing env → empty strings (caller decides whether to abort)."""
        mod = _load_module()
        app_key, app_secret, cano, acnt_prdt_cd = mod.resolve_kis_credentials({})
        assert app_key == ""
        assert app_secret == ""
        assert cano == ""
        # acnt_prdt_cd default is "01" (KIS convention) — caller treats empty cano as missing
        assert acnt_prdt_cd == "01"

    def test_credit_number_without_dash_falls_back_to_separate_vars(self):
        """If HANTOO_CREDIT_NUMBER missing dash, fall through to KIS_CANO/KIS_ACNT_PRDT_CD."""
        mod = _load_module()
        env = {
            "HANTOO_FAKE_API_KEY": "k",
            "HANTOO_FAKE_SECRET_API_KEY": "s",
            "KIS_CANO": "55555555",
            "KIS_ACNT_PRDT_CD": "03",
        }
        app_key, app_secret, cano, acnt_prdt_cd = mod.resolve_kis_credentials(env)
        assert cano == "55555555"
        assert acnt_prdt_cd == "03"

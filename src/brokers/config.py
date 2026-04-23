from __future__ import annotations

import os
import re

from pydantic import BaseModel, field_validator, model_validator

from src.brokers.errors import ConfigurationError

_CREDIT_RE = re.compile(r"^[0-9]{8}-[0-9]{2}$")

_REQUIRED_VARS = [
    "HANTOO_FAKE_API_KEY",
    "HANTOO_FAKE_SECRET_API_KEY",
    "HANTOO_CREDIT_NUMBER",
    "BINANCE_DEMO_API_KEY",
    "BINANCE_DEMO_SECRET_API_KEY",
    "BINANCE_BASE_URL",
    "BINANCE_WS_URL",
]


class BrokerConfig(BaseModel):
    hantoo_fake_api_key: str
    hantoo_fake_secret_api_key: str
    hantoo_credit_number: str
    hantoo_cano: str = ""
    hantoo_acnt_prdt_cd: str = ""
    binance_demo_api_key: str
    binance_demo_secret_api_key: str
    binance_base_url: str
    binance_ws_url: str
    active_broker: str = "kis"

    @model_validator(mode="after")
    def parse_credit_number(self) -> "BrokerConfig":
        raw = self.hantoo_credit_number
        if not _CREDIT_RE.match(raw):
            raise ConfigurationError(
                f"HANTOO_CREDIT_NUMBER='{raw}' does not match expected format "
                f"^[0-9]{{8}}-[0-9]{{2}}$ (e.g. '12345678-01')"
            )
        parts = raw.split("-")
        self.hantoo_cano = parts[0]
        self.hantoo_acnt_prdt_cd = parts[1]
        return self


def load_broker_config() -> BrokerConfig:
    """Load broker configuration from environment variables.

    Raises ConfigurationError with a clear message for any missing or
    malformed variable.
    """
    missing = [k for k in _REQUIRED_VARS if not os.environ.get(k)]
    if missing:
        raise ConfigurationError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            f"Copy .env.example to .env and fill in the values."
        )

    try:
        return BrokerConfig(
            hantoo_fake_api_key=os.environ["HANTOO_FAKE_API_KEY"],
            hantoo_fake_secret_api_key=os.environ["HANTOO_FAKE_SECRET_API_KEY"],
            hantoo_credit_number=os.environ["HANTOO_CREDIT_NUMBER"],
            binance_demo_api_key=os.environ["BINANCE_DEMO_API_KEY"],
            binance_demo_secret_api_key=os.environ["BINANCE_DEMO_SECRET_API_KEY"],
            binance_base_url=os.environ["BINANCE_BASE_URL"],
            binance_ws_url=os.environ["BINANCE_WS_URL"],
            active_broker=os.environ.get("ACTIVE_BROKER", "kis"),
        )
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError(str(exc)) from exc

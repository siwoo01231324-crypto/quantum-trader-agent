from __future__ import annotations

import logging
import re

# Patterns that must be masked in log messages.
_KEY_PATTERNS = [
    r"api_key",
    r"secret",
    r"signature",
    r"authorization",
    r"appkey",
    r"appsecret",
    r"hashkey",
    r"cano",
    r"approval_key",
    r"access_token",
]

# Matches key=value or key="value" or key: value patterns
_MASK_RE = re.compile(
    r"(?i)(?:"
    + "|".join(_KEY_PATTERNS)
    + r')\s*[=:"\s]\s*["\']?([A-Za-z0-9+/=._\-:]{4,})["\']?',
    re.IGNORECASE,
)

# URL query signature param
_SIG_QUERY_RE = re.compile(r"(?i)((?:&|\?)signature=)([A-Za-z0-9+/=%]{4,})")

# Bearer token — covers full JWT (includes dots between header.payload.sig)
_BEARER_RE = re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9+/=._\-]{4,}(?:\.[A-Za-z0-9+/=._\-]+)*)")


def _mask(text: str) -> str:
    # Apply bearer first so full JWT is masked before authorization key pattern fires
    text = _BEARER_RE.sub(r"\g<1>***", text)
    text = _MASK_RE.sub(lambda m: m.group(0).replace(m.group(1), "***"), text)
    text = _SIG_QUERY_RE.sub(r"\g<1>***", text)
    return text


class SecretMaskingFilter(logging.Filter):
    """logging.Filter that redacts secrets from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _mask(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _mask(str(v)) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_mask(str(a)) for a in record.args)
        return True


def install_global() -> None:
    """Attach SecretMaskingFilter to the root logger (call once at startup)."""
    root = logging.getLogger()
    for f in root.filters:
        if isinstance(f, SecretMaskingFilter):
            return
    root.addFilter(SecretMaskingFilter())

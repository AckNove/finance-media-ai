"""证据核查工具（`fact_check`）。"""

from backend.app.capabilities.verification.tool import (
    MIN_QUOTE_CHARS,
    check_quote,
    find_snapshot,
    make_fact_check,
    register_verification_adapters,
)

__all__ = [
    "MIN_QUOTE_CHARS",
    "check_quote",
    "find_snapshot",
    "make_fact_check",
    "register_verification_adapters",
]

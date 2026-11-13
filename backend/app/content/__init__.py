"""内容层：一稿多平台的适配（只做排版与长度，不产生事实）。"""

from backend.app.content.platform_adapt import (
    PLATFORMS,
    SPECS,
    Adapted,
    PlatformSpec,
    adapt,
    adapt_one,
    adapt_payload,
)

__all__ = [
    "PLATFORMS",
    "SPECS",
    "Adapted",
    "PlatformSpec",
    "adapt",
    "adapt_one",
    "adapt_payload",
]

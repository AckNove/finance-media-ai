"""音视频工具链能力层（V2.2 Phase 1）。

本包只解决"用哪个 ffmpeg、它能做什么"，不解决"怎么剪辑"。
剪辑编排、素材调度与渲染由后续阶段的 Video Engine Adapter 负责。

对外入口统一从这里导入，避免业务代码到处 `from ...media.ffmpeg import ...`：

    from backend.app.capabilities.media import resolve_toolchain, probe_capabilities
"""

from backend.app.capabilities.media.ffmpeg import (
    MIN_FFMPEG_VERSION,
    MIN_FFMPEG_VERSION_TEXT,
    PROJECT_BIN_DIR,
    Capability,
    CapabilityReport,
    MediaToolError,
    MediaToolchain,
    probe_capabilities,
    read_version,
    require_toolchain,
    resolve_toolchain,
    run_ffmpeg,
    run_ffprobe,
    summarise,
)

__all__ = [
    "MIN_FFMPEG_VERSION",
    "MIN_FFMPEG_VERSION_TEXT",
    "PROJECT_BIN_DIR",
    "Capability",
    "CapabilityReport",
    "MediaToolError",
    "MediaToolchain",
    "probe_capabilities",
    "read_version",
    "require_toolchain",
    "resolve_toolchain",
    "run_ffmpeg",
    "run_ffprobe",
    "summarise",
]

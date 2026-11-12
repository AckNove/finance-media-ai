"""视频交接（研究 → 视频任务目录）。"""

from backend.app.video.handoff import (
    JOBS_ROOT,
    JOB_DIRS,
    VideoHandoffError,
    VideoJobScaffold,
    create_video_job,
)

__all__ = [
    "JOBS_ROOT",
    "JOB_DIRS",
    "VideoHandoffError",
    "VideoJobScaffold",
    "create_video_job",
]

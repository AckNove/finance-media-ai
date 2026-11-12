"""研究工作流的数据层：证据汇编（digest）与原文核对（verify）。

这一层刻意**不碰模型**：输入是已抓取的真实资料，输出是带来源编号的底稿与核对结果。
任何需要判断力（分析、归因、结论）的步骤都属于 Agent/模型，不在这里伪装。
"""

from backend.app.research.digest import (
    SCHEMA_VERSION,
    DataPoint,
    Digest,
    SourceRef,
    build_digest,
    extract_data_points,
    sources_from_collect,
    write_digest,
)
from backend.app.research.verify import verify_points

__all__ = [
    "SCHEMA_VERSION",
    "DataPoint",
    "Digest",
    "SourceRef",
    "build_digest",
    "extract_data_points",
    "sources_from_collect",
    "verify_points",
    "write_digest",
]

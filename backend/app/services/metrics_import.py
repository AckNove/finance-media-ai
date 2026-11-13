"""账号数据的 CSV 批量导入（平台后台导出的那种表）。

为什么不写在 service 里：解析要能单独测（列名千奇百怪、数字带千分位、
时间格式不统一），所以拆成"纯函数解析 + service 落库"。

三条纪律：
1. **历史不覆盖**——同一 ``captured_at`` 已经有快照就跳过，并在结果里说清楚为什么跳过；
2. **错行不吞**——解析失败的行带行号返回，不静默丢弃，也不猜测缺失的数字；
3. **平台不对就拒绝**——CSV 里的 ``platform`` 必须和这条排期的平台一致，
   混进来就直接报错，免得把抖音的数据记到小红书名下。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime

#: 认得的列名（同一含义允许多种写法，平台导出各不相同）
CAPTURED_AT_KEYS = ("captured_at", "采集时间", "date", "日期", "time", "时间")
PLATFORM_KEYS = ("platform", "平台")
COUNT_KEYS = {
    "views": ("views", "播放量", "播放", "阅读量", "view"),
    "likes": ("likes", "点赞", "点赞数", "like"),
    "comments": ("comments", "评论", "评论数", "comment"),
    "shares": ("shares", "分享", "转发", "share"),
    "followers_delta": ("followers_delta", "涨粉", "粉丝变化", "followers"),
}
#: 一次最多导入多少行（防止有人把十万行的表直接怼进来）
MAX_ROWS = 500


@dataclass
class ParsedRow:
    """一行解析成功的快照。"""

    line: int
    captured_at: datetime
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    followers_delta: int = 0
    platform: str = ""


@dataclass
class ImportResult:
    """导入结果：写进去的、跳过的、报错的，分三堆说清楚。"""

    imported: list[ParsedRow] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "imported": len(self.imported),
            "skipped": len(self.skipped),
            "errors": len(self.errors),
            "rows": [
                {
                    "line": row.line,
                    "captured_at": row.captured_at.isoformat(),
                    "views": row.views,
                    "likes": row.likes,
                    "comments": row.comments,
                    "shares": row.shares,
                    "followers_delta": row.followers_delta,
                }
                for row in self.imported
            ],
            "skipped_detail": list(self.skipped),
            "error_detail": list(self.errors),
            "note": (
                "跳过的是「这个时间点已经有快照」的行（历史不覆盖）；"
                "报错的行没有被导入，也没有被猜数字"
                if (self.skipped or self.errors)
                else "全部导入"
            ),
        }


class CsvImportError(ValueError):
    """整份 CSV 都不能用（缺列、空表、行数超限）。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _pick(header: dict[str, int], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key in header:
            return header[key]
    return None


def _to_int(raw: str, *, field_name: str, line: int) -> int:
    text = (raw or "").strip().replace(",", "").replace("，", "")
    if text in {"", "-", "—"}:
        return 0
    try:
        value = int(float(text))
    except ValueError as error:
        raise ValueError(f"第 {line} 行的 {field_name} 不是数字：{raw!r}") from error
    if value < 0:
        raise ValueError(f"第 {line} 行的 {field_name} 是负数：{value}")
    return value


def _to_time(raw: str, *, line: int) -> datetime:
    text = (raw or "").strip()
    if not text:
        raise ValueError(f"第 {line} 行没有采集时间")
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"第 {line} 行的采集时间看不懂：{raw!r}") from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_metrics_csv(text: str, *, platform: str = "") -> ImportResult:
    """把 CSV 文本解析成快照行；整份不可用时抛 :class:`CsvImportError`。"""

    body = (text or "").strip()
    if not body:
        raise CsvImportError("empty_csv", "CSV 是空的")
    reader = csv.reader(io.StringIO(body))
    rows = list(reader)
    if not rows:
        raise CsvImportError("empty_csv", "CSV 是空的")
    header = {cell.strip().lower(): index for index, cell in enumerate(rows[0])}
    time_index = _pick(header, CAPTURED_AT_KEYS)
    if time_index is None:
        raise CsvImportError(
            "missing_column",
            "找不到采集时间列：需要一个叫 captured_at / 采集时间 / date / 日期的列",
        )
    platform_index = _pick(header, PLATFORM_KEYS)
    count_index = {name: _pick(header, keys) for name, keys in COUNT_KEYS.items()}
    if all(index is None for index in count_index.values()):
        raise CsvImportError(
            "missing_column",
            "一个数据列都没有：至少要 views/播放量、likes/点赞 之类的一列",
        )
    if len(rows) - 1 > MAX_ROWS:
        raise CsvImportError("too_many_rows", f"一次最多导入 {MAX_ROWS} 行，这份有 {len(rows) - 1} 行")

    result = ImportResult()
    for offset, raw_row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in raw_row):
            continue
        try:
            captured_at = _to_time(raw_row[time_index] if time_index < len(raw_row) else "", line=offset)
            row = ParsedRow(line=offset, captured_at=captured_at)
            for name, index in count_index.items():
                if index is None or index >= len(raw_row):
                    continue
                setattr(row, name, _to_int(raw_row[index], field_name=name, line=offset))
            if platform_index is not None and platform_index < len(raw_row):
                row.platform = raw_row[platform_index].strip()
            if platform and row.platform and row.platform != platform:
                raise ValueError(
                    f"第 {offset} 行的平台是 {row.platform!r}，和这条排期的 {platform!r} 不一致"
                )
            result.imported.append(row)
        except ValueError as error:
            result.errors.append({"line": offset, "reason": str(error)})
    if not result.imported and not result.errors:
        raise CsvImportError("no_rows", "表头之后没有任何数据行")
    return result


__all__ = [
    "CAPTURED_AT_KEYS",
    "COUNT_KEYS",
    "MAX_ROWS",
    "CsvImportError",
    "ImportResult",
    "ParsedRow",
    "parse_metrics_csv",
]

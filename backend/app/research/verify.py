"""把"候选数据点"拿回原文核对——只做程序能确定的那部分。

程序能确定的是：**这句话是否真的出现在该来源的抓取正文里**。
程序不能确定的是：这句话是否成立（统计口径、时间范围、样本、口径变化……）。
所以这里的结论只有两种：

* ``found``：原话在正文里逐字出现——只能说明"引用没抄错"；
* ``missing``：原话在摘录里找不到——可能是摘录被截断（``EXCERPT_CHARS``），
  也可能真的引错了。**标成待人工复核，不下"造假"的结论。**

不做模糊匹配拼凑：用逐字包含，宁可多报 missing，也不把"看起来差不多"当成对上。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

#: 比对时忽略的空白与全/半角差异（正文抽取常把空格吃掉）
_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    return _WHITESPACE.sub("", text or "")


def verify_points(digest: Any) -> dict[str, Any]:
    """逐条核对 ``digest.data_points`` 是否出现在其来源的摘录里。"""
    excerpts = _excerpts_from_digest(digest)
    results: list[dict[str, Any]] = []
    found = 0
    for point in getattr(digest, "data_points", ()) or ():
        source_excerpt = excerpts.get(point.source_id, "")
        needle = _normalise(point.text)
        hit = bool(needle) and needle in _normalise(source_excerpt)
        if hit:
            found += 1
        results.append(
            {
                "source_id": point.source_id,
                "text": point.text,
                "numbers": list(point.numbers),
                "status": "found" if hit else "missing",
                "note": "" if hit else "摘录里找不到原话（可能被截断，或引用有误），需人工复核",
            }
        )
    return {
        "checked": len(results),
        "found": found,
        "missing": len(results) - found,
        "results": results,
    }


def _excerpts_from_digest(digest: Any) -> dict[str, str]:
    meta = getattr(digest, "meta", None) or {}
    excerpts = meta.get("excerpts")
    if isinstance(excerpts, Mapping):
        return {str(key): str(value) for key, value in excerpts.items()}
    return {}


__all__ = ["verify_points"]

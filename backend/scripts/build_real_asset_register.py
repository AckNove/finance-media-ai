"""把候选素材清单转成"可以直接给人看的登记表"（CSV）。

任务书要求每项真实素材登记 11 个字段：
素材ID / 原始文件 / 来源网址 / 拍摄主体或发布机构 / 授权条款 /
是否允许商业使用 / 是否允许剪辑修改 / 是否需要署名 / 使用在哪个镜头 /
下载时间 / 文件Hash。

这份脚本只做转换与判定，不替人拍板：
- 「是否允许商业使用」直接重算，不看别人写好的结论；
- 「是否需要署名」按授权判定（CC0 / 公有领域=不需要；CC BY=必须署名）；
- 「使用在哪个镜头」默认留空，由分镜确定后再回填（空着就是没定，不是"忘了填"）。

用法：
    python -m backend.scripts.build_real_asset_register
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_DIR = PROJECT_ROOT / "content" / "jobs" / "v6_power_first_film" / "remaster_v2" / "assets" / "real"
CANDIDATES = REAL_DIR / "candidates.json"
REGISTER = REAL_DIR / "source_register.csv"

# 分镜定了就用哪一项；没定的留空（空着表示"还没定"，不是"忘了填"）
SHOT_USAGE: dict[str, str] = {
    "R01": "A1",
    "R02": "B1",
    "R05": "D1",
    "R06": "E1",
    "R07": "E1(备选)",
}

COLUMNS = (
    "asset_id",
    "original_file",
    "source_url",
    "publisher_or_subject",
    "license",
    "commercial_use",
    "modification_allowed",
    "attribution_required",
    "used_in_shot",
    "downloaded_at",
    "sha256",
    "local_path",
    "note",
)

# 授权 → (可商用, 可改编, 需署名)
LICENSE_RULES: tuple[tuple[str, bool, bool, bool], ...] = (
    ("cc0", True, True, False),
    ("public domain", True, True, False),
    ("cc by 4.0", True, True, True),
    ("cc by 3.0", True, True, True),
    ("cc by 2.5", True, True, True),
    ("cc by 2.0", True, True, True),
)


def _rule(license_short: str) -> tuple[bool, bool, bool]:
    key = (license_short or "").strip().lower()
    for prefix, commercial, modify, attribution in LICENSE_RULES:
        if prefix in key:
            return commercial, modify, attribution
    return False, False, True


def build() -> list[dict[str, str]]:
    payload = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    for index, result in enumerate(payload["results"], start=1):
        chosen = result.get("chosen") or {}
        if result.get("status") != "downloaded" or not chosen:
            continue
        local = PROJECT_ROOT / chosen["local_path"]
        digest = chosen.get("sha256") or (hashlib.sha256(local.read_bytes()).hexdigest() if local.exists() else "")
        commercial, modify, attribution = _rule(chosen.get("license_short", ""))
        rows.append(
            {
                "asset_id": f"R{index:02d}",
                "original_file": chosen.get("title", "").removeprefix("File:"),
                "source_url": chosen.get("page_url", ""),
                "publisher_or_subject": (chosen.get("artist") or "").split("\n")[0].strip(),
                "license": chosen.get("license_short", ""),
                "commercial_use": "allowed" if commercial else "forbidden",
                "modification_allowed": "yes" if modify else "no",
                "attribution_required": "yes" if attribution else "no",
                "used_in_shot": SHOT_USAGE.get(f"R{index:02d}", ""),
                "downloaded_at": chosen.get("retrieved_at", ""),
                "sha256": digest,
                "local_path": chosen["local_path"],
                "note": f"slot={result['slot']}",
            }
        )
    return rows


def main() -> int:
    rows = build()
    REGISTER.parent.mkdir(parents=True, exist_ok=True)
    with REGISTER.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    sys.stdout.reconfigure(encoding="utf-8")
    print(
        json.dumps(
            {
                "register": str(REGISTER.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "rows": len(rows),
                "commercial_allowed": sum(1 for r in rows if r["commercial_use"] == "allowed"),
                "need_attribution": [r["asset_id"] for r in rows if r["attribution_required"] == "yes"],
                "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

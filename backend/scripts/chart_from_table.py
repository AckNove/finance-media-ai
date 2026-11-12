"""从一张表算出指标、画出图，并把图登记进任务素材清单（V5.0）。

用法：
    python -m backend.scripts.chart_from_table --job <job_id> \
        --input path/to/table.csv --label-column 年份 --value-column 净息差 \
        --kind line --title "商业银行净息差" --source-note "来源：<机构> <文件>"

它只做四件事，每件都留下可核对的痕迹：
1. 读表（CSV / XLSX / TSV），数字列用 :mod:`backend.app.analysis.metrics` 解析；
2. 算几个指标（首末值、首末变化、同比、CAGR、占比）并打印成 JSON；
3. 用 :mod:`backend.app.analysis.charts` 画成固定尺寸的 PNG（同输入同字节）；
4. 把 PNG 登记成任务素材清单里的 B 类资产（含 SHA-256），分镜才能引用它。

刻意不做：不猜单位、不补缺失值、不把"看起来像趋势"的话写进输出。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.app.analysis.charts import bar_chart, line_chart, share_bar
from backend.app.analysis.metrics import cagr, column, describe, share
from backend.app.core.config import PROJECT_ROOT
from backend.app.video.generated import register_generated_asset
from backend.app.capabilities.adapters.structured import extract_structured

JOB_ROOT = PROJECT_ROOT / "content" / "jobs"

CHART_KINDS = ("line", "bar", "share")


def _rows_from(path: Path) -> list[dict[str, str]]:
    document = extract_structured(path)
    if document.tables:
        table = document.tables[0]
        header = list(table.header)
        return [dict(zip(header, row)) for row in table.rows]
    raise SystemExit(f"表里没有可读的数据区域：{path}")


def _metrics(points: list[tuple[str, float]]) -> dict[str, object]:
    values = [value for _, value in points]
    result: dict[str, object] = {
        "count": len(values),
        "first": values[0] if values else None,
        "last": values[-1] if values else None,
        "change": round(values[-1] - values[0], 6) if len(values) >= 2 else None,
    }
    if len(values) >= 2 and values[0]:
        result["change_pct"] = round((values[-1] - values[0]) / abs(values[0]) * 100, 2)
    if len(values) >= 2:
        growth = cagr(values[0], values[-1], len(values) - 1)
        result["cagr"] = None if growth is None else round(growth, 6)
    total = sum(value for value in values if value > 0)
    shares = [share(value, total) for value in values] if total else []
    result["shares"] = [None if item is None else round(item, 2) for item in shares]
    result["describe"] = describe(values)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="表格数据 → 指标 → 图表 → 素材清单")
    parser.add_argument("--job", required=True, help="content/jobs 下的任务 id")
    parser.add_argument("--input", required=True, help="CSV / TSV / XLSX 路径")
    parser.add_argument("--label-column", required=True)
    parser.add_argument("--value-column", required=True)
    parser.add_argument("--kind", default="line", choices=CHART_KINDS)
    parser.add_argument("--title", default="")
    parser.add_argument("--unit", default="")
    parser.add_argument("--source-note", default="", help="图上必须印出的出处行")
    parser.add_argument("--name", default="", help="输出文件名（不含后缀）")
    parser.add_argument("--jobs-root", default=str(JOB_ROOT))
    args = parser.parse_args(argv)

    job_root = Path(args.jobs_root) / args.job
    if not job_root.exists():
        raise SystemExit(f"任务目录不存在：{job_root}")

    source_table = Path(args.input)
    if not source_table.exists():
        raise SystemExit(f"输入表不存在：{source_table}")

    rows = _rows_from(source_table)
    values = column(rows, args.value_column, label_column=args.label_column)
    points: list[tuple[str, float]] = []
    for label, value in zip(values.labels, values.values):
        if value is None:
            continue
        points.append((str(label), float(value)))
    if len(points) < 2:
        raise SystemExit("有效数据点少于 2 个，画不出有意义的图（不做补齐）")

    charts_dir = job_root / "assets" / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    stem = args.name or f"chart_{args.value_column}"
    output = charts_dir / f"{stem}.png"
    note = args.source_note or f"来源：{source_table.name}"

    if args.kind == "line":
        result = line_chart(
            [(args.title or args.value_column, [value for _, value in points])],
            [label for label, _ in points],
            output=output,
            title=args.title,
            unit=args.unit,
            source_note=note,
        )
    elif args.kind == "bar":
        result = bar_chart(
            [label for label, _ in points],
            [value for _, value in points],
            output=output,
            title=args.title,
            unit=args.unit,
            source_note=note,
        )
    else:
        result = share_bar(
            [(label, value) for label, value in points],
            output=output,
            title=args.title,
            source_note=note,
        )

    asset = register_generated_asset(
        job_root,
        result.path,
        kind="image",
        purpose=f"{args.title or stem}（{args.kind}）",
    )

    payload = {
        "job_id": args.job,
        "input": str(source_table),
        "kind": args.kind,
        "points": [{"label": label, "value": value} for label, value in points],
        "metrics": _metrics(points),
        "chart": {
            "path": asset.file,
            "sha256": asset.sha256,
            "byte_size": asset.byte_size,
            "width": result.width,
            "height": result.height,
        },
        "asset_id": asset.asset_id,
        "registered_as": "B",
        "note": "指标由本脚本按固定规则算出；图表同输入同字节，可重算核对",
    }
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

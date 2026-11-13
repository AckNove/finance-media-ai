"""按任务目录里已有的研究结论，生成一张产业链示意图（画板）。

用法：

```powershell
python -m backend.scripts.chain_diagram --job v6_accept_chain_demo
python -m backend.scripts.chain_diagram --job <id> --asset-id chain_01 --seconds 5
```

它只读 ``<job>/research/analysis.json``（模型分析产物）里的结论，按产业链骨架的
upstream / midstream / downstream 三行取节点；挂不上骨架的结论不上图。图写进
``<job>/assets/compositions/<asset-id>.json`` 并登记进 ``assets_manifest.csv``
（B 类、授权 allowed），之后分镜里用 ``asset_type=composition`` + ``asset_id`` 引用它即可。

**这个脚本不渲染**：渲染由 ``render_job`` 统一做（它会把组合文档导入工程再出帧）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT
from backend.app.video.chain import nodes_from_findings, write_chain

DEFAULT_ROOT = PROJECT_ROOT / "content" / "jobs"
ANALYSIS_FILE = Path("research") / "analysis.json"


def _arg(name: str, fallback: str | None = None) -> str | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(f"--{name}")
    known, _ = parser.parse_known_args()
    value = getattr(known, name)
    return value if value is not None else fallback


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成产业链示意图画板（不渲染）")
    parser.add_argument("--job", required=True, help="任务目录名（content/jobs 下）")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="任务目录根路径")
    parser.add_argument("--asset-id", default="chain_01", help="资产编号（默认 chain_01）")
    parser.add_argument("--title", default="", help="图上标题（默认用分析产物的目标）")
    parser.add_argument("--seconds", type=float, default=5.0, help="画板时长（秒）")
    parser.add_argument("--fps", type=int, default=30, help="帧率")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args(argv)

    job_root = Path(args.root).resolve() / args.job
    if not job_root.is_dir():
        print(json.dumps({"error": "unknown_job", "job": args.job}, ensure_ascii=False))
        return 2
    analysis_path = job_root / ANALYSIS_FILE
    if not analysis_path.is_file():
        print(
            json.dumps(
                {
                    "error": "no_analysis",
                    "path": str(analysis_path),
                    "next_step": "这份任务还没有模型分析产物：先跑完 research.analyze 再来画图",
                },
                ensure_ascii=False,
            )
        )
        return 3
    payload = json.loads(analysis_path.read_text(encoding="utf-8"))
    findings = payload.get("findings") or []
    stages = nodes_from_findings(findings)
    if not any(stages.values()):
        print(
            json.dumps(
                {
                    "error": "no_chain_sections",
                    "detail": "结论里没有挂到 upstream/midstream/downstream 的句子，画不出产业链图",
                    "hint": "把研究目标写成产业链问题（例如「XX 产业链利润为什么向两端流动」）再跑一次",
                },
                ensure_ascii=False,
            )
        )
        return 4

    frames = max(2, int(round(args.seconds * args.fps)))
    title = args.title or str(payload.get("objective") or "") or f"{args.job} 产业链"
    document = write_chain(
        job_root,
        asset_id=args.asset_id,
        title=title,
        stages=stages,
        frames=frames,
        fps=args.fps,
    )
    result = {
        "ok": True,
        "asset_id": document.asset_id,
        "file": document.relative_path,
        "frames": document.frames,
        "seconds": round(document.frames / args.fps, 3),
        "nodes": {key: list(value) for key, value in stages.items()},
        "note": "只生成画板，未渲染；分镜里用 asset_type=composition 引用它",
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"已生成产业链图：{document.relative_path}（{document.frames} 帧）")
        for key, value in stages.items():
            print(f"  {key}: {value or '（本环缺证据）'}")
        print("下一步：分镜里引用它，再跑 render_job")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

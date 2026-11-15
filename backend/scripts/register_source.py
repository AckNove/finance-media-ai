"""把一份真实素材登记进任务的白名单（`<job>/input/approved_sources/`）。

为什么要有这条命令：真实素材能不能进片，取决于三件事——**文件在不在白名单目录里、
哈希对不对得上、许可允不允许商用**。这三件事以前只有代码入口（`ApprovedSources.register`），
等于"知道规则的人才能用"，而最需要它的人（拿到一张截图想用的人）恰恰不会写代码。

几条口径（和渲染前预检共用同一套判断）：
- 文件会被**复制**进 `input/approved_sources/`，原始位置不动；
- 同名文件已存在且内容不同 → 拒绝（`name_taken`），不覆盖别人的素材；
- 同名文件已存在且哈希一致 → 直接复用那一份，不重复复制；
- `--commercial-use` 不是 `allowed` 时照样登记，但会明确告诉你**预检会拦住它**；
- 登记结果写进 `source_register.csv`，哈希是**登记那一刻**算的。

退出码：0 登记成功 / 1 被规则拒绝 / 2 用法问题（任务目录不存在、文件不存在等）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backend.app.core.config import PROJECT_ROOT
from backend.app.core.job_paths import (
    APPROVED_DIRNAME,
    COMMERCIAL_USE_VALUES,
    VERIFICATION_STATUSES,
    ApprovedSources,
    PathNotAllowedError,
)

DEFAULT_JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"


def _sha256_of(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_job(job_id: str, jobs_root: Path) -> Path:
    job_dir = (jobs_root / job_id).resolve()
    if not job_dir.is_dir():
        raise PathNotAllowedError("unknown_job", f"任务目录不存在：{job_dir}", path=str(job_dir))
    return job_dir


def _place_file(source: Path, approved_dir: Path, *, copy: bool) -> tuple[Path, str]:
    """把文件放进白名单目录，返回 (最终路径, 动作)。动作是 copied / reused / already_inside。"""

    target = approved_dir / source.name
    if source.resolve() == target.resolve() if target.exists() else False:
        return target, "already_inside"
    if target.exists():
        if _sha256_of(target) == _sha256_of(source):
            return target, "reused"
        raise PathNotAllowedError(
            "name_taken",
            f"白名单里已经有同名文件且内容不同：{target.name}（不覆盖别人的素材，请改个文件名或换 source_id）",
            path=str(target),
        )
    if not copy:
        raise PathNotAllowedError(
            "not_inside",
            f"文件不在白名单目录里，而 --no-copy 又没允许复制：{source}",
            path=str(source),
        )
    approved_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target, "copied"


def _summary(entry: Any) -> dict[str, Any]:
    payload = asdict(entry)
    payload["path"] = str(payload.get("path", ""))
    # asdict 里的字段叫 license_name（CSV 列名是 license），这里两个名字都给，免得下游各猜一次
    payload["license"] = payload.get("license_name", "")
    payload["usable"] = payload.get("commercial_use") == "allowed"
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.scripts.register_source",
        description="把真实素材登记进任务白名单（记哈希、许可与来源）",
    )
    parser.add_argument("--job", required=True, help="视频任务号（content/jobs 下的目录名）")
    parser.add_argument("--jobs-root", default=str(DEFAULT_JOBS_ROOT), help="任务根目录（默认 content/jobs）")
    parser.add_argument("--file", help="要登记的素材文件路径（可以不在任务目录里）")
    parser.add_argument("--source-id", help="素材编号，如 S01")
    parser.add_argument("--source-name", help="素材名称（写进片尾署名的就是它）")
    parser.add_argument("--publisher", default="", help="发布方/机构")
    parser.add_argument("--source-url", default="", help="原始链接")
    parser.add_argument("--published-at", default="", help="发布日期 YYYY-MM-DD")
    parser.add_argument("--data-as-of", default="", help="数据截止日 YYYY-MM-DD")
    parser.add_argument("--unit", default="", help="计量单位（表格/图里是什么单位）")
    parser.add_argument("--methodology", default="", help="口径说明")
    parser.add_argument("--license", dest="license_name", default="", help="许可名称（如 CC0 / 已获授权 / 官方公开发布）")
    parser.add_argument(
        "--commercial-use",
        default="unknown",
        choices=list(COMMERCIAL_USE_VALUES),
        help="能不能商用；只有 allowed 才会被允许进片",
    )
    parser.add_argument(
        "--verification-status",
        default="unverified",
        choices=list(VERIFICATION_STATUSES),
        help="核对状态（默认 unverified，核对过再改成 verified）",
    )
    parser.add_argument("--credit", default="", help="署名文字")
    parser.add_argument("--no-copy", action="store_true", help="只登记已在白名单目录里的文件，不复制")
    parser.add_argument("--list", action="store_true", help="只列出这个任务已登记的素材")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    jobs_root = Path(args.jobs_root)
    if not jobs_root.is_absolute():
        jobs_root = (PROJECT_ROOT / jobs_root).resolve()
    try:
        job_dir = _resolve_job(args.job, jobs_root)
        approved_dir = job_dir / "input" / APPROVED_DIRNAME
        register = ApprovedSources(job_dir)
        # 先把已经登记过的读进来：不读的话，第二次登记会把清单覆盖成只剩这一行。
        # 读的时候会重新算每份文件的哈希，登记后被改过的文件在这里就会被拒。
        try:
            register.load_register()
        except PathNotAllowedError as error:
            if error.reason != "not_registered":
                raise

        if args.list:
            entries = [_summary(item) for item in register.entries]
            if args.json:
                print(json.dumps(entries, ensure_ascii=False, indent=2))
                return 0
            if not entries:
                print(f"{args.job}：还没有登记过任何素材（目录 {approved_dir}）")
                return 0
            print(f"{args.job}：已登记 {len(entries)} 份素材")
            for item in entries:
                verdict = "可进片" if item["usable"] and item["license"] else f"预检会拦（commercial_use={item['commercial_use']}）"
                print(f"  {item['source_id']}  {item['source_name']}")
                print(f"      {item['original_filename']}  {item['byte_size']} B  sha256={item['sha256'][:12]}…  {verdict}")
            return 0

        missing = [name for name in ("file", "source_id", "source_name") if not getattr(args, name)]
        if missing:
            flags = "、".join("--" + name.replace("_", "-") for name in missing)
            print(f"登记没开始：缺参数 {flags}（只想看已登记的用 --list）", file=sys.stderr)
            return 2

        source = Path(args.file)
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        if not source.is_file():
            print(f"登记没开始：文件不存在 -> {source}", file=sys.stderr)
            return 2

        target, action = _place_file(source, approved_dir, copy=not args.no_copy)
        entry = register.register(
            target,
            source_id=args.source_id,
            source_name=args.source_name,
            publisher=args.publisher,
            source_url=args.source_url,
            published_at=args.published_at,
            data_as_of=args.data_as_of,
            unit=args.unit,
            methodology=args.methodology,
            verification_status=args.verification_status,
            license_name=args.license_name,
            commercial_use=args.commercial_use,
            credit=args.credit,
        )
        register_path = register.write_register()
        payload = _summary(entry)
        payload["action"] = action
        payload["register"] = str(register_path)
        payload["relative"] = str(target.relative_to(job_dir))

        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"已登记 {entry.source_id}：{entry.source_name}")
            print(f"  文件：{payload['relative']}（{action}，{entry.byte_size} B）")
            print(f"  哈希：sha256={entry.sha256[:16]}…（登记后改文件会被预检拦住）")
            print(f"  许可：{entry.license_name or '（没写）'} · 可商用={entry.commercial_use}")
            print(f"  清单：{register_path}")
            if entry.commercial_use != "allowed":
                print("  ⚠ 这份素材的 commercial_use 不是 allowed：渲染前预检会拦住它，不会进片。")
                print("    确认可以商用后，用 --commercial-use allowed 重新登记一次。")
            elif not entry.license_name:
                print("  ⚠ 这份素材没写许可名：预检要求「可商用 + 有许可记录」两条都满足才放行。")
                print("    用 --license 补一句话（如「官方公开发布」「已获授权」）。")
        return 0
    except PathNotAllowedError as error:
        print(f"登记被拒（{error.reason}）：{error}", file=sys.stderr)
        # 「任务目录不存在」是用法问题，不是规则拒绝：让人一眼看出是路径写错了
        return 2 if error.reason == "unknown_job" else 1


if __name__ == "__main__":
    raise SystemExit(main())

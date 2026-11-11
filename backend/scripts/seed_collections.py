"""把六大内容合集写入 ``topic_columns``（幂等，可重复执行）。

设计依据：``docs/research_hub/database_design.md`` §0 决策 D2、§6.3。

为什么单独一个脚本而不是写在迁移里：
迁移是"结构变更"，合集是"业务主数据"。把主数据写进迁移会让
``alembic check`` 永远显示漂移，也让新增合集必须发一个新迁移。
所以：**结构在迁移里，主数据在本脚本里**。

幂等规则（按 ``code`` upsert）：
- 不存在则插入；
- 已存在则只更新 name / description / display_order / is_system / is_active /
  taxonomy_version，**绝不修改 id**（``topics.column_id`` 与
  ``topic_collections.column_id`` 都指向它）。

安全阈值：本脚本拒绝在**真实开发库**上执行，除非显式加 ``--allow-development-db``。
M2.0 阶段禁止向 ``data/finance_media.db`` 写入任何数据。

用法::

    # 演练（只看结果，不提交）
    python -m backend.scripts.seed_collections --database-url sqlite:///C:/tmp/t.db --dry-run

    # 真正写入（必须是明确的库）
    python -m backend.scripts.seed_collections --database-url sqlite:///C:/tmp/t.db
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings, load_yaml_config
from backend.app.database.session import build_engine
from backend.app.models.topic import TopicColumn

#: 合集定义版本号：写进 ``topic_columns.taxonomy_version``，
#: 方便日后判断哪些行来自哪一版分类体系。
TAXONOMY_VERSION = 1

#: 本脚本拒绝写入的库（结构基线，M2.0 禁止改动）
PROTECTED_DB_SUFFIX = "data/finance_media.db"


def load_collections() -> list[dict[str, Any]]:
    """从 ``config/content_taxonomy.yaml`` 读取合集定义（唯一真相）。"""
    config = load_yaml_config("content_taxonomy.yaml")
    collections = config.get("collections")
    if not collections:
        raise ValueError("content_taxonomy.yaml 里没有 collections 段")
    return list(collections)


def seed_collections(engine, *, dry_run: bool = False) -> dict[str, int]:
    """按 ``code`` 幂等 upsert 六大合集，返回统计。"""
    collections = load_collections()
    created = 0
    updated = 0
    unchanged = 0

    with Session(engine) as session:
        for index, item in enumerate(collections):
            code = str(item["id"])
            name = str(item["name"])
            description = item.get("mission")
            row = session.scalar(select(TopicColumn).where(TopicColumn.code == code))
            if row is None:
                session.add(
                    TopicColumn(
                        code=code,
                        name=name,
                        description=description,
                        display_order=index,
                        is_active=True,
                        is_system=True,
                        taxonomy_version=TAXONOMY_VERSION,
                    )
                )
                created += 1
                continue

            changed = False
            if row.name != name:
                row.name = name
                changed = True
            if description is not None and row.description != description:
                row.description = description
                changed = True
            if row.display_order != index:
                row.display_order = index
                changed = True
            if not row.is_system:
                row.is_system = True
                changed = True
            if not row.is_active:
                row.is_active = True
                changed = True
            if row.taxonomy_version != TAXONOMY_VERSION:
                row.taxonomy_version = TAXONOMY_VERSION
                changed = True
            updated += 1 if changed else 0
            unchanged += 0 if changed else 1

        if dry_run:
            session.rollback()
        else:
            session.commit()

    return {
        "total": len(collections),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
    }


def resolve_database_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    return get_settings().resolved_database_url()


def is_protected(url: str) -> bool:
    normalized = url.replace("\\", "/").lower()
    return normalized.endswith(PROTECTED_DB_SUFFIX)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="写入六大内容合集（幂等）")
    parser.add_argument("--database-url", default=None, help="目标库 URL，缺省用 settings")
    parser.add_argument("--dry-run", action="store_true", help="只演练，不提交")
    parser.add_argument(
        "--allow-development-db",
        action="store_true",
        help="显式允许写入真实开发库 data/finance_media.db（M2.0 阶段不要使用）",
    )
    args = parser.parse_args(argv)

    url = resolve_database_url(args.database_url)
    if is_protected(url) and not args.allow_development_db:
        print(f"拒绝执行：{url} 是受保护的真实开发库，需要 --allow-development-db")
        return 2

    engine = build_engine(Settings(database_url=url))
    try:
        stats = seed_collections(engine, dry_run=args.dry_run)
    finally:
        engine.dispose()

    mode = "dry-run" if args.dry_run else "committed"
    print(
        f"seed_collections[{mode}] url={url} "
        f"total={stats['total']} created={stats['created']} "
        f"updated={stats['updated']} unchanged={stats['unchanged']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

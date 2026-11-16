"""按分镜表生成 V2 的实拍镜头页（可重复执行，输出幂等）。

镜头密度按"巫师财经式"的节奏来配：18 秒里放 6 个画面，平均 3 秒一换，
但旁白是连续的——观众听到的是一段完整论述，看到的是一直在换的画面。

文字规则（与 `direction/visual_language.md` 一致）：
- kicker 说明"这是什么地方"，title 是这一镜的主张；
- 每个镜头都带「资料画面」角标与作者署名——授权要求的署名由登记表决定，不靠人工记；
- 数据层只用**有依据**的东西（两条收入线来自 S02 容量电价机制，不写没核实过的百分比）。

用法：
    python -m backend.scripts.build_v2_scenes            # 全建
    python -m backend.scripts.build_v2_scenes --only B1_wide
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.video.cinema.photo import (  # noqa: E402
    PhotoMove,
    PhotoOverlay,
    PhotoScene,
    PhotoText,
    build_photo_scene,
)

JOB = PROJECT_ROOT / "content" / "jobs" / "v6_power_first_film"
REM = JOB / "remaster_v2"
SCENES = REM / "production" / "scenes"
REGISTER = REM / "assets" / "real" / "source_register.csv"

ACCENT = "#b6ff1f"
COOL = "#4d86a8"


@dataclass(frozen=True)
class Shot:
    shot_id: str
    asset_id: str
    duration: float
    grade: str
    kicker: str
    title: str
    move: PhotoMove
    title_y: float = 0.66
    kicker_y: float = 0.615
    title_size: int = 62
    overlays: tuple[PhotoOverlay, ...] = ()
    extra_texts: tuple[PhotoText, ...] = ()


# 顺序按分镜表；两镜用同一张素材，靠不同裁切与运镜做出"切点"
SHOTS: tuple[Shot, ...] = (
    Shot(
        shot_id="A1_wide",
        asset_id="R09",  # 灞桥电厂（中国西安），CC0
        duration=3.0,
        grade="cinema",
        kicker="真实电厂 · 资料画面",
        title="发电量在掉",
        move=PhotoMove(z0=1.04, z1=1.12, x0=0.0, x1=0.012, y0=0.01, y1=-0.01),
        title_y=0.70,
        kicker_y=0.655,
        title_size=84,
    ),
    Shot(
        shot_id="A1_tight",
        asset_id="R09",
        duration=2.6,
        grade="cinema",
        kicker="同一座电厂 · 冷却塔与升压站",
        title="电费收入却还在涨",
        move=PhotoMove(z0=1.95, z1=2.12, x0=-0.06, x1=-0.03, y0=0.05, y1=0.02),
        title_y=0.70,
        kicker_y=0.655,
        title_size=84,
    ),
    Shot(
        shot_id="B1_wide",
        asset_id="R02",  # 牵引变电站（CC BY 4.0，需署名）
        duration=3.4,
        grade="industrial",
        kicker="真实变电设备 · 资料画面",
        title="一条线卖电，另一条线卖「待命」",
        move=PhotoMove(z0=1.10, z1=1.02, x0=-0.015, x1=0.02, y0=0.0, y1=-0.01),
        title_y=0.735,
        kicker_y=0.688,
        title_size=58,
        overlays=(
            # 数据区暗衬：让柱子和标签在真实设备画面上读得清（电影里做数据层的常规手法）
            PhotoOverlay("scrim", "scrim", 0.04, 0.268, 0.92, 0.252,
                         color="rgba(4,8,14,0.52)", at=0.05, grow=0.35),
            PhotoOverlay("base", "rule", 0.08, 0.505, 0.84, 0.0018, color="rgba(242,245,247,0.55)", at=0.25, grow=0.4),
            # 刻度：给数据层一个"量程"，不是随便浮着两根方块
            PhotoOverlay("tick1", "tick", 0.36, 0.505, 0.0022, 0.055, color="rgba(242,245,247,0.30)", at=0.4, grow=0.3),
            PhotoOverlay("tick2", "tick", 0.78, 0.505, 0.0022, 0.055, color="rgba(242,245,247,0.30)", at=0.45, grow=0.3),
            PhotoOverlay(
                "bar_energy", "bar", 0.17, 0.505, 0.075, 0.105,
                color=COOL, label="电量收入", note="按发电量结算", at=0.35, grow=0.75, label_y=0.345, label_color="#f2f5f7",
            ),
            PhotoOverlay(
                "bar_capacity", "bar", 0.62, 0.505, 0.075, 0.175,
                color=ACCENT, label="容量收入", note="按待命能力结算", at=0.5, grow=1.0, label_y=0.292, label_color="#f2f5f7",
            ),
        ),
    ),
    Shot(
        shot_id="B1_tight",
        asset_id="R02",
        duration=2.8,
        grade="industrial",
        kicker="同一座变电站 · 设备特写",
        title="两种钱，两套结算",
        move=PhotoMove(z0=1.75, z1=1.90, x0=0.05, x1=0.02, y0=-0.02, y1=0.0),
        title_y=0.80,
        kicker_y=0.755,
        title_size=58,
        overlays=(
            PhotoOverlay("cal_tank", "callout", 0.13, 0.30, 0.0025, 0.10, color="rgba(182,255,31,0.85)", at=0.5, grow=0.5,
                         label="变压器 · 容量在这里计量", label_side="below"),
            PhotoOverlay("cal_bus", "callout", 0.60, 0.26, 0.0025, 0.14, color="rgba(77,134,168,0.9)", at=0.75, grow=0.5,
                         label="母线 · 电量从这里走", label_side="below"),
        ),
    ),
    Shot(
        shot_id="E1_close",
        asset_id="R11",  # 鞍山输电铁塔（CC BY 2.0）
        duration=3.0,
        grade="dusk",
        kicker="真实输电线路 · 资料画面",
        title="钱没有变多，只是分法变了",
        move=PhotoMove(z0=1.35, z1=1.22, x0=0.02, x1=-0.01, y0=0.0, y1=-0.015),
        title_y=0.70,
        kicker_y=0.655,
        title_size=58,
    ),
)


def build(only: tuple[str, ...] = ()) -> list[dict]:
    receipts: list[dict] = []
    for shot in SHOTS:
        if only and shot.shot_id not in only:
            continue
        scene = PhotoScene(
            scene_id=shot.shot_id,
            asset_id=shot.asset_id,
            photo_rel="photo.jpg",
            duration=shot.duration,
            move=shot.move,
            texts=(
                PhotoText("kicker", shot.kicker, shot.kicker_y, at=0.12, size=30),
                PhotoText("title", shot.title, shot.title_y, at=0.3, size=shot.title_size),
                *shot.extra_texts,
            ),
            overlays=shot.overlays,
            label="资料画面",
            grade=shot.grade,
        )
        receipts.append(
            build_photo_scene(
                scene,
                project_root=PROJECT_ROOT,
                scene_dir=SCENES / shot.shot_id,
                register_csv=REGISTER,
            )
        )
    return receipts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 V2 实拍镜头页")
    parser.add_argument("--only", nargs="*", default=[], help="只建这些镜头（按 shot_id）")
    args = parser.parse_args(argv)
    receipts = build(tuple(args.only))
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"built": len(receipts), "scenes": receipts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""按分镜表生成 V2 的实拍镜头页（可重复执行，输出幂等）。

分镜表 `remaster_v2/storyboard/storyboard_v2.csv` 里 `layer=real_footage` 或 `mixed` 的镜头，
由这里生成自包含镜头目录；`layer=three_d` 的镜头由 `v2_substation.py` 生成，不在这里。

文字规则（与 `direction/visual_language.md` 一致）：
- kicker 说明"这是什么地方"，title 是这一镜的主张；
- 每个镜头都带「资料画面」角标与作者署名——授权要求的署名由登记表决定，不靠人工记。

用法：
    python -m backend.scripts.build_v2_scenes            # 全建
    python -m backend.scripts.build_v2_scenes --only A1  # 只建一镜
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
    overlays: tuple[PhotoOverlay, ...] = ()


# 顺序按分镜表；文字只保留"这一镜在说什么"，不堆句子
SHOTS: tuple[Shot, ...] = (
    Shot(
        shot_id="A1_plant",
        asset_id="R09",  # 灞桥电厂（中国西安），CC0
        duration=5.4,
        grade="cinema",
        kicker="真实电厂 · 资料画面",
        title="发电量在掉，\n电费收入却还在涨",
        move=PhotoMove(z0=1.06, z1=1.20, x0=0.0, x1=0.015, y0=0.02, y1=-0.02),
    ),
    Shot(
        shot_id="B1_substation",
        asset_id="R02",  # 铁路牵引变电站（CC BY 4.0，需署名）
        duration=6.2,
        grade="industrial",
        kicker="真实变电设备 · 资料画面",
        title="一条线卖电，\n另一条线卖「待命」",
        move=PhotoMove(z0=1.12, z1=1.02, x0=-0.02, x1=0.03, y0=0.0, y1=-0.01),
        title_y=0.615,
        kicker_y=0.567,
        overlays=(
            # 基准线：两条收入线都从这条地面起算
            PhotoOverlay("base", "rule", 0.08, 0.505, 0.84, 0.0018, color="rgba(242,245,247,0.5)", at=0.4, grow=0.5),
            # 左：电量收入——跟着发电量走，越走越低
            PhotoOverlay(
                "bar_energy", "bar", 0.20, 0.505, 0.16, 0.115,
                color="#4d86a8", label="电量收入", note="按发电量结算", at=0.7, grow=1.1,
            ),
            # 右：容量收入——按可用的"待命能力"付费，往上长
            PhotoOverlay(
                "bar_capacity", "bar", 0.62, 0.505, 0.16, 0.185,
                color="#b6ff1f", label="容量收入", note="按待命能力结算", at=0.9, grow=1.4,
            ),
        ),
    ),
    Shot(
        shot_id="D1_industry",
        asset_id="R12",  # 北京夜间工业（CC BY 2.0）
        duration=5.6,
        grade="industrial",
        kicker="真实工业用电 · 资料画面",
        title="利润在降，\n说明这是存量再分配",
        move=PhotoMove(z0=1.10, z1=1.18, x0=0.01, x1=-0.01, y0=-0.02, y1=0.02),
    ),
    Shot(
        shot_id="E1_pylon",
        asset_id="R11",  # 鞍山输电铁塔（CC BY 2.0）
        duration=5.2,
        grade="dusk",
        kicker="真实输电线路 · 资料画面",
        title="钱没有变多，\n只是分法变了",
        move=PhotoMove(z0=1.16, z1=1.03, x0=0.02, x1=-0.01, y0=0.0, y1=-0.015),
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
                PhotoText("kicker", shot.kicker, shot.kicker_y, at=0.15),
                PhotoText("title", shot.title, shot.title_y, at=0.45, size=62),
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

"""生成素材登记：自绘图表/示意图进入任务素材清单，但改不了外部素材的规则（V5.0）。

覆盖的自由：
* 任务目录内的自绘 PNG 能被登记成 B 类（我们自己画的），`commercial_use=allowed`；
  同一路径重复登记只更新一行，不产生重复；
* 已登记行里的 `used_in_shots`（分镜引用关系）在重建时保留，`purpose` 不会被空值冲掉；
* 任务目录之外的文件一律拒绝（`outside_job`），空文件拒绝（`empty_file`），
  缺文件拒绝（`missing`）。

不变的红线：
* A 类（他人拍摄/拍摄片段）仍然只能来自 `input/approved_sources/` 的登记表，
  且 `commercial_use != allowed` 时渲染前就会被 `assert_renderable` 拦下；
* 本模块不参与"这个图表对不对"的判断，只保证它出现在清单里、哈希可核对。
"""

from __future__ import annotations

import pytest

from backend.app.media.manifest import read_manifest
from backend.app.video.generated import (
    GENERATED_LICENSE,
    GENERATED_VERIFICATION,
    GeneratedAssetError,
    register_generated_asset,
    sha256_of,
)


@pytest.fixture()
def job(tmp_path):
    root = tmp_path / "content" / "jobs" / "demo_job"
    (root / "assets" / "charts").mkdir(parents=True)
    return root


def _png(job, name="chart_01_net_interest.png", payload=b"\x89PNG\r\n\x1a\n" + b"0" * 256):
    path = job / "assets" / "charts" / name
    path.write_bytes(payload)
    return path


def test_generated_chart_is_listed_as_class_b(job):
    path = _png(job)

    asset = register_generated_asset(job, path, kind="image", purpose="净息差十年走势")

    assert asset.asset_class == "B"
    assert asset.commercial_use == "allowed", "自己画的图不需要外部授权"
    assert asset.license == GENERATED_LICENSE
    assert asset.verification_status == GENERATED_VERIFICATION
    assert asset.sha256 == sha256_of(path)
    assert asset.byte_size == path.stat().st_size
    assert asset.file == "assets/charts/chart_01_net_interest.png"

    rows = read_manifest(job / "assets_manifest.csv")
    assert len(rows) == 1
    assert rows[0]["asset_id"] == "chart_01_net_interest"
    assert rows[0]["purpose"] == "净息差十年走势"


def test_registering_twice_updates_one_row_and_keeps_shot_usage(job):
    path = _png(job)
    register_generated_asset(job, path, kind="image", used_in_shots="sh03, sh04")

    # 重建时换了字节、没传 purpose 与镜头号
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 128)
    again = register_generated_asset(job, path, kind="image")

    rows = read_manifest(job / "assets_manifest.csv")
    assert len(rows) == 1, "同一路径只应有一行"
    assert rows[0]["used_in_shots"] == "sh03, sh04", "分镜引用不该被重建清空"
    assert rows[0]["sha256"] == again.sha256 == sha256_of(path)


def test_an_existing_purpose_survives_an_empty_update(job):
    path = _png(job)
    register_generated_asset(job, path, kind="image", purpose="收入结构")
    register_generated_asset(job, path, kind="image", purpose="")

    rows = read_manifest(job / "assets_manifest.csv")
    assert rows[0]["purpose"] == "收入结构"


def test_a_file_outside_the_job_is_refused(tmp_path, job):
    outside = tmp_path / "somewhere" / "chart.png"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"2" * 64)

    with pytest.raises(GeneratedAssetError) as error:
        register_generated_asset(job, outside, kind="image")
    assert error.value.code == "outside_job"


def test_relative_escape_is_refused(job):
    sneaky = job / "assets" / "charts" / ".." / ".." / ".." / "escape.png"
    (job.parent.parent / "escape.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"3" * 64)

    with pytest.raises(GeneratedAssetError) as error:
        register_generated_asset(job, sneaky, kind="image")
    assert error.value.code == "outside_job"


def test_missing_and_empty_files_are_refused(job):
    with pytest.raises(GeneratedAssetError) as missing:
        register_generated_asset(job, job / "assets" / "charts" / "nope.png", kind="image")
    assert missing.value.code == "missing"

    empty = job / "assets" / "charts" / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(GeneratedAssetError) as blank:
        register_generated_asset(job, empty, kind="image")
    assert blank.value.code == "empty_file"


def test_manifest_keeps_the_phase2_columns(job):
    path = _png(job)
    register_generated_asset(job, path, kind="image")

    header = (job / "assets_manifest.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == [
        "asset_id",
        "asset_class",
        "kind",
        "file",
        "source_id",
        "publisher",
        "source_url",
        "retrieved_at",
        "sha256",
        "license",
        "commercial_use",
        "credit",
        "verification_status",
        "used_in_shots",
        "purpose",
    ]

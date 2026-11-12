"""Register our own generated visuals in a job's asset manifest (V5.0).

The Phase 2 manifest is built *from the approved-sources registry*: a file that was
never registered cannot appear in a film. That rule is right for footage somebody
else shot, but charts and diagrams we compute ourselves have no external source —
they are authored here, from data that is already in the evidence chain.

This module adds the missing half without weakening the original rule:

* the file must live **inside the job directory** (after ``resolve()``, so ``..``,
  symlinks and absolute escapes are rejected the same way as everywhere else);
* an existing row for the same relative path is updated in place, so re-running a
  build does not produce duplicate rows, and the ``used_in_shots`` a storyboard
  already filled in is preserved;
* ``asset_class`` is fixed to ``B`` (``B`` = authored by us) and
  ``commercial_use`` to ``allowed`` — we own the output, and the numeric inputs
  stay traceable through ``source_id``/``purpose``.

Nothing here decides that a chart *is* correct; it only makes it listable.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path

from backend.app.media.manifest import MANIFEST_COLUMNS, MANIFEST_FILENAME

DEFAULT_CREDIT = "本仓库用 matplotlib 生成（数据来自证据链）"
GENERATED_LICENSE = "self-generated"
GENERATED_VERIFICATION = "self_generated"


class GeneratedAssetError(ValueError):
    """The generated file cannot be listed honestly."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GeneratedAsset:
    asset_id: str
    asset_class: str
    kind: str
    file: str
    sha256: str
    byte_size: int
    license: str
    commercial_use: str
    credit: str
    verification_status: str
    used_in_shots: str
    purpose: str

    def as_row(self) -> dict[str, str]:
        return {
            "asset_id": self.asset_id,
            "asset_class": self.asset_class,
            "kind": self.kind,
            "file": self.file,
            "source_id": self.asset_id,
            "publisher": "本仓库",
            "source_url": "",
            "retrieved_at": "",
            "sha256": self.sha256,
            "license": self.license,
            "commercial_use": self.commercial_use,
            "credit": self.credit,
            "verification_status": self.verification_status,
            "used_in_shots": self.used_in_shots,
            "purpose": self.purpose,
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.as_row(), "byte_size": self.byte_size}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path(job_root: Path | str) -> Path:
    return Path(job_root) / MANIFEST_FILENAME


def _relative_inside(job_root: Path, path: Path) -> str:
    root = job_root.resolve()
    target = Path(path).resolve()
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise GeneratedAssetError(
            "outside_job", f"{target} 不在任务目录 {root} 之内，不能登记为素材"
        ) from error
    if not target.is_file():
        raise GeneratedAssetError("missing", f"文件不存在：{target}")
    if target.stat().st_size <= 0:
        raise GeneratedAssetError("empty_file", f"文件是空的：{target}")
    return relative.as_posix()


def register_generated_asset(
    job_root: Path | str,
    path: Path | str,
    *,
    kind: str,
    purpose: str = "",
    credit: str = DEFAULT_CREDIT,
    used_in_shots: str = "",
    asset_id: str | None = None,
) -> GeneratedAsset:
    """Append or update one generated row in ``<job>/assets_manifest.csv``."""
    root = Path(job_root)
    file_value = _relative_inside(root, Path(path))
    target = root.resolve() / file_value
    digest = sha256_of(target)
    resolved_id = asset_id or Path(file_value).stem

    manifest = manifest_path(root)
    rows: list[dict[str, str]] = []
    if manifest.exists():
        with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = [dict(row) for row in csv.DictReader(handle)]

    previous = next((row for row in rows if row.get("file") == file_value), None)
    asset = GeneratedAsset(
        asset_id=resolved_id,
        asset_class="B",
        kind=kind,
        file=file_value,
        sha256=digest,
        byte_size=target.stat().st_size,
        license=GENERATED_LICENSE,
        commercial_use="allowed",
        credit=credit,
        verification_status=GENERATED_VERIFICATION,
        # 分镜写进去的镜头号要留住，重建一次不该把引用关系清空
        used_in_shots=(used_in_shots or (previous or {}).get("used_in_shots", "")),
        purpose=(purpose or (previous or {}).get("purpose", "")),
    )

    if previous is not None:
        previous.update({key: value for key, value in asset.as_row().items() if value != ""})
        previous["used_in_shots"] = asset.used_in_shots
        previous["purpose"] = asset.purpose
    else:
        rows.append(asset.as_row())

    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in MANIFEST_COLUMNS})
    return asset


__all__ = [
    "DEFAULT_CREDIT",
    "GENERATED_LICENSE",
    "GENERATED_VERIFICATION",
    "GeneratedAsset",
    "GeneratedAssetError",
    "manifest_path",
    "register_generated_asset",
    "sha256_of",
]

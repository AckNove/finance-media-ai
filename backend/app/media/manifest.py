"""Assets manifest: what a film is made of, and under whose terms (V2.2 Phase 2).

Every picture and every sound in a render is listed here with the facts a reviewer
needs: where it came from, its SHA-256, its licence, whether commercial use is
allowed, and which shots used it. The manifest is generated from the job's
:class:`~backend.app.core.job_paths.ApprovedSources` registry, so a file that was
never registered cannot appear — and a file whose ``commercial_use`` is not
``allowed`` cannot be rendered at all.

Asset classes (from the V2.1 report, unchanged):

``A``  licensed real media — footage or photography somebody actually shot;
``B``  original visuals we authored — compositions, charts, diagrams, score;
``C``  auxiliary assets — fonts and other support files that are not pictures.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from backend.app.core.job_paths import ApprovedSources, PathNotAllowedError

MANIFEST_FILENAME = "assets_manifest.csv"

MANIFEST_COLUMNS = (
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
)

ASSET_CLASSES = ("A", "B", "C")


class ManifestError(ValueError):
    """The manifest cannot be written without lying about an asset."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManifestRow:
    asset_id: str
    asset_class: str
    kind: str
    file: str
    source_id: str
    publisher: str
    source_url: str
    retrieved_at: str
    sha256: str
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
            "source_id": self.source_id,
            "publisher": self.publisher,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "sha256": self.sha256,
            "license": self.license,
            "commercial_use": self.commercial_use,
            "credit": self.credit,
            "verification_status": self.verification_status,
            "used_in_shots": self.used_in_shots,
            "purpose": self.purpose,
        }


def build_manifest(
    sources: ApprovedSources,
    usage: Mapping[str, Iterable[str]],
    *,
    classes: Mapping[str, str],
    kinds: Mapping[str, str],
    purposes: Mapping[str, str] | None = None,
    require_commercial: bool = True,
) -> list[ManifestRow]:
    """Join the registry with per-shot usage into manifest rows.

    ``usage`` maps ``source_id`` → shot ids; ``classes`` maps ``source_id`` → A/B/C.
    """
    rows: list[ManifestRow] = []
    for source_id, shots in usage.items():
        try:
            entry = sources.get(source_id)
        except PathNotAllowedError as error:
            raise ManifestError("unregistered", f"{source_id} is not in the source register: {error}") from error
        asset_class = classes.get(source_id)
        if asset_class not in ASSET_CLASSES:
            raise ManifestError("bad_class", f"{source_id}: asset_class must be one of {ASSET_CLASSES}")
        kind = kinds.get(source_id, "unknown")
        if require_commercial and asset_class in {"A", "B"}:
            sources.assert_renderable(source_id)
        # Publish the path the reviewer can open: job-relative when the file lives
        # inside the job, otherwise the registered filename. A bare filename would
        # force the reader to guess which directory it belongs to.
        try:
            file_value = str(entry.path.relative_to(sources.job_root)).replace("\\", "/")
        except ValueError:
            file_value = entry.original_filename
        rows.append(
            ManifestRow(
                asset_id=source_id,
                asset_class=asset_class,
                kind=kind,
                file=file_value,
                source_id=source_id,
                publisher=entry.publisher,
                source_url=entry.source_url,
                retrieved_at=entry.retrieved_at,
                sha256=entry.sha256,
                license=entry.license_name,
                commercial_use=entry.commercial_use,
                credit=entry.credit,
                verification_status=entry.verification_status,
                used_in_shots=", ".join(sorted(set(shots))),
                purpose=(purposes or {}).get(source_id, ""),
            )
        )
    return rows


def write_manifest(path: Path | str, rows: Iterable[ManifestRow]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_row())
    return target


def read_manifest(path: Path | str) -> list[dict[str, str]]:
    target = Path(path)
    if not target.exists():
        raise ManifestError("missing", f"manifest not found: {target}")
    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


__all__ = [
    "ASSET_CLASSES",
    "MANIFEST_COLUMNS",
    "MANIFEST_FILENAME",
    "ManifestError",
    "ManifestRow",
    "build_manifest",
    "read_manifest",
    "write_manifest",
]

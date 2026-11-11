"""Job-scoped source whitelist.

Every research job keeps its inputs inside ``content/jobs/<job_id>/input/approved_sources``.
Nothing else may be read into research: not the desktop, not another job, not an old
export. This module is the single gate that enforces it.

The rules are deliberately boring and testable:

* a path is resolved to an absolute, normalised form before anything else happens;
* ``..`` never escapes, because the resolved path is compared against the job root;
* symlinks are refused outright (a link is a path that lies about where it points);
* a file must be registered before it can be resolved, so an agent cannot walk the
  directory and quietly use whatever it finds;
* each registration records where the bytes came from and their SHA-256, so the same
  file name cannot smuggle different bytes into a second job.

This is a whitelist for *this* process. It is not an OS sandbox: a caller that ignores
this module can still open any file it likes with plain ``open()``. Tools registered in
``backend.app.capabilities.tools`` are expected to call :meth:`ApprovedSources.resolve`
instead of accepting a raw path, which is what makes the gate meaningful in practice.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

APPROVED_DIRNAME = "approved_sources"
REGISTER_FILENAME = "source_register.csv"

REGISTER_COLUMNS = (
    "source_id",
    "source_name",
    "original_filename",
    "publisher",
    "source_url",
    "published_at",
    "data_as_of",
    "retrieved_at",
    "unit",
    "methodology",
    "original_file_hash",
    "verification_status",
    "byte_size",
    "license",
    "commercial_use",
    "credit",
)

VERIFICATION_STATUSES = ("unverified", "in_review", "verified", "disputed", "rejected")

#: Commercial-use verdict per source. Only ``allowed`` may reach a rendered video:
#: a file whose rights are merely plausible is not a licence to publish.
COMMERCIAL_USE_VALUES = ("allowed", "not_allowed", "unclear", "unknown")


class PathNotAllowedError(PermissionError):
    """A path was refused by the whitelist.

    ``reason`` is a stable code so callers and tests can assert on it without
    parsing prose: ``outside_job``, ``traversal``, ``symlink``, ``not_registered``,
    ``not_a_file``, ``missing``, ``empty_file``, ``invalid_status``.
    """

    def __init__(self, reason: str, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.path = path


@dataclass(frozen=True)
class ApprovedSource:
    """One registered input file, with the provenance a claim needs."""

    source_id: str
    source_name: str
    path: Path
    original_filename: str
    sha256: str
    byte_size: int
    publisher: str = ""
    source_url: str = ""
    published_at: str = ""
    data_as_of: str = ""
    retrieved_at: str = ""
    unit: str = ""
    methodology: str = ""
    verification_status: str = "unverified"
    license_name: str = ""
    commercial_use: str = "unknown"
    credit: str = ""

    def as_row(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "original_filename": self.original_filename,
            "publisher": self.publisher,
            "source_url": self.source_url,
            "published_at": self.published_at,
            "data_as_of": self.data_as_of,
            "retrieved_at": self.retrieved_at,
            "unit": self.unit,
            "methodology": self.methodology,
            "original_file_hash": self.sha256,
            "verification_status": self.verification_status,
            "byte_size": str(self.byte_size),
            "license": self.license_name,
            "commercial_use": self.commercial_use,
            "credit": self.credit,
        }


@dataclass
class ApprovedSources:
    """Register and resolve the inputs of a single job directory."""

    job_root: Path
    _entries: dict[str, ApprovedSource] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.job_root = Path(self.job_root).expanduser().absolute()
        if self.job_root.is_symlink():
            raise PathNotAllowedError("symlink", f"job root is a symlink: {self.job_root}")

    # ---------------------------------------------------------------- locations
    @property
    def approved_dir(self) -> Path:
        return self.job_root / "input" / APPROVED_DIRNAME

    @property
    def register_path(self) -> Path:
        return self.job_root / REGISTER_FILENAME

    # ------------------------------------------------------------------ checks
    def _normalise(self, path: Path) -> Path:
        raw = Path(path)
        if any(part == ".." for part in raw.parts):
            raise PathNotAllowedError("traversal", f"path contains '..': {raw}", path=str(raw))
        candidate = raw if raw.is_absolute() else self.approved_dir / raw
        resolved = candidate.expanduser().absolute()
        try:
            resolved = resolved.resolve(strict=False)
        except OSError as error:  # pragma: no cover - platform specific
            raise PathNotAllowedError("missing", f"cannot resolve {raw}: {error}", path=str(raw)) from error
        try:
            resolved.relative_to(self.job_root.resolve())
        except ValueError as error:
            raise PathNotAllowedError(
                "outside_job", f"{resolved} is outside the job directory {self.job_root}",
                path=str(resolved)) from error
        return resolved

    def _reject_symlinks(self, path: Path) -> None:
        current = path
        while True:
            if current.is_symlink():
                raise PathNotAllowedError(
                    "symlink", f"symlink is not allowed in the source path: {current}",
                    path=str(current))
            if current == self.job_root or current.parent == current:
                return
            current = current.parent

    def _inside_approved(self, path: Path) -> Path:
        approved = self.approved_dir.resolve()
        try:
            path.resolve().relative_to(approved)
        except ValueError as error:
            raise PathNotAllowedError(
                "outside_job", f"{path} is not inside {APPROVED_DIRNAME}", path=str(path)) from error
        return path

    # -------------------------------------------------------------- registering
    def register(
        self,
        path: Path | str,
        *,
        source_id: str,
        source_name: str,
        publisher: str = "",
        source_url: str = "",
        published_at: str = "",
        data_as_of: str = "",
        unit: str = "",
        methodology: str = "",
        verification_status: str = "unverified",
        retrieved_at: str | None = None,
        license_name: str = "",
        commercial_use: str = "unknown",
        credit: str = "",
    ) -> ApprovedSource:
        if verification_status not in VERIFICATION_STATUSES:
            raise PathNotAllowedError(
                "invalid_status",
                f"verification_status must be one of {VERIFICATION_STATUSES}", path=str(path))
        if commercial_use not in COMMERCIAL_USE_VALUES:
            raise PathNotAllowedError(
                "invalid_license",
                f"commercial_use must be one of {COMMERCIAL_USE_VALUES}", path=str(path))
        resolved = self._normalise(Path(path))
        self._inside_approved(resolved)
        self._reject_symlinks(resolved)
        if not resolved.exists():
            raise PathNotAllowedError("missing", f"file does not exist: {resolved}", path=str(resolved))
        if not resolved.is_file():
            raise PathNotAllowedError("not_a_file", f"not a regular file: {resolved}", path=str(resolved))
        size = resolved.stat().st_size
        if size == 0:
            raise PathNotAllowedError("empty_file", f"file is empty: {resolved}", path=str(resolved))
        entry = ApprovedSource(
            source_id=source_id,
            source_name=source_name,
            path=resolved,
            original_filename=resolved.name,
            sha256=_sha256(resolved),
            byte_size=size,
            publisher=publisher,
            source_url=source_url,
            published_at=published_at,
            data_as_of=data_as_of,
            retrieved_at=retrieved_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            unit=unit,
            methodology=methodology,
            verification_status=verification_status,
            license_name=license_name,
            commercial_use=commercial_use,
            credit=credit,
        )
        self._entries[source_id] = entry
        return entry

    # ---------------------------------------------------------------- licensing
    def assert_renderable(self, source_id: str) -> ApprovedSource:
        """Refuse a source whose commercial use is not affirmatively allowed."""
        entry = self.get(source_id)
        if entry.commercial_use != "allowed":
            raise PathNotAllowedError(
                "rights_unclear",
                f"{source_id} has commercial_use={entry.commercial_use!r}; "
                "only 'allowed' sources may enter a rendered video",
                path=str(entry.path))
        if not entry.license_name:
            raise PathNotAllowedError(
                "rights_unclear",
                f"{source_id} has no recorded licence; a rendered video needs one",
                path=str(entry.path))
        return entry

    # --------------------------------------------------------------- resolving
    def resolve(self, path: Path | str) -> ApprovedSource:
        """Return the registered entry for ``path`` or refuse it."""
        resolved = self._normalise(Path(path))
        self._inside_approved(resolved)
        self._reject_symlinks(resolved)
        for entry in self._entries.values():
            if entry.path == resolved:
                if _sha256(resolved) != entry.sha256:
                    raise PathNotAllowedError(
                        "not_registered",
                        f"{resolved} changed after registration (hash mismatch)", path=str(resolved))
                return entry
        raise PathNotAllowedError(
            "not_registered",
            f"{resolved} is not a registered source; register it before reading",
            path=str(resolved))

    def get(self, source_id: str) -> ApprovedSource:
        try:
            return self._entries[source_id]
        except KeyError as error:
            raise PathNotAllowedError("not_registered", f"unknown source_id {source_id}") from error

    @property
    def entries(self) -> tuple[ApprovedSource, ...]:
        return tuple(self._entries.values())

    # ---------------------------------------------------------------- register
    def write_register(self) -> Path:
        target = self.register_path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REGISTER_COLUMNS)
            writer.writeheader()
            for entry in self._entries.values():
                writer.writerow(entry.as_row())
        return target

    def load_register(self) -> "ApprovedSources":
        """Re-read ``source_register.csv`` and re-verify every file on disk.

        A later stage runs in another process, so it cannot inherit the in-memory
        registry. Rather than trusting the CSV blindly, every file is re-hashed and
        compared with the hash recorded at registration: a file that changed after
        the register was written is refused, not quietly re-described.
        """
        target = self.register_path
        if not target.exists():
            raise PathNotAllowedError("not_registered", f"no source register at {target}")
        entries: dict[str, ApprovedSource] = {}
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                source_id = (row.get("source_id") or "").strip()
                filename = (row.get("original_filename") or "").strip()
                if not source_id or not filename:
                    raise PathNotAllowedError("not_registered", f"register row without id or filename: {row}")
                path = self._normalise(self.approved_dir / filename)
                self._inside_approved(path)
                self._reject_symlinks(path)
                if not path.is_file():
                    raise PathNotAllowedError(
                        "missing", f"{path} is registered but not on disk", path=str(path))
                actual = _sha256(path)
                declared = (row.get("original_file_hash") or "").strip()
                if declared and actual != declared:
                    raise PathNotAllowedError(
                        "not_registered",
                        f"{path} changed after registration (hash mismatch)", path=str(path))
                entries[source_id] = ApprovedSource(
                    source_id=source_id,
                    source_name=row.get("source_name", ""),
                    path=path,
                    original_filename=filename,
                    sha256=actual,
                    byte_size=int(row.get("byte_size") or path.stat().st_size),
                    publisher=row.get("publisher", ""),
                    source_url=row.get("source_url", ""),
                    published_at=row.get("published_at", ""),
                    data_as_of=row.get("data_as_of", ""),
                    retrieved_at=row.get("retrieved_at", ""),
                    unit=row.get("unit", ""),
                    methodology=row.get("methodology", ""),
                    verification_status=row.get("verification_status") or "unverified",
                    license_name=row.get("license", ""),
                    commercial_use=row.get("commercial_use") or "unknown",
                    credit=row.get("credit", ""),
                )
        self._entries = entries
        return self


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

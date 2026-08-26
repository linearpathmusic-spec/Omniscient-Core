#!/usr/bin/env python3
"""Validated backup and rollback-safe restore for durable Brain state."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

BRAIN_ROOT = Path(__file__).resolve().parent.parent
BACKUPS_DIR = BRAIN_ROOT / "backups"
MANIFEST_NAME = "backup-manifest.json"
MANIFEST_VERSION = 1
MAX_BACKUP_FILES = 20_000
MAX_MEMBER_SIZE = 32 * 1024 * 1024
MAX_TOTAL_SIZE = 256 * 1024 * 1024

SNAPSHOT_DIRS = (
    "knowledge", "raw", "skills", "history", "proposals", "brain",
    "tools", "state", "evals",
)
SNAPSHOT_FILES = ("AGENTS.md", "README.md", "pyrightconfig.json")
ALLOWED_TOP_LEVEL = frozenset((*SNAPSHOT_DIRS, *SNAPSHOT_FILES, MANIFEST_NAME))


def _set_root(root: Path) -> None:
    """Override the root for isolated tests."""
    global BRAIN_ROOT, BACKUPS_DIR
    BRAIN_ROOT = root
    BACKUPS_DIR = root / "backups"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@contextmanager
def _corpus_lock() -> Iterator[None]:
    """Coordinate snapshots/restores with BrainWriter and BrainReviewer."""
    lock_dir = BRAIN_ROOT / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with open(lock_dir / "knowledge-mutations.lock", "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _snapshot_files() -> list[Path]:
    files: list[Path] = []
    for name in SNAPSHOT_DIRS:
        directory = BRAIN_ROOT / name
        if directory.exists():
            files.extend(p for p in directory.rglob("*") if p.is_file() and not p.is_symlink())
    files.extend(
        BRAIN_ROOT / name for name in SNAPSHOT_FILES
        if (BRAIN_ROOT / name).is_file() and not (BRAIN_ROOT / name).is_symlink()
    )
    return sorted(files, key=lambda p: p.as_posix())


def create_backup() -> Path:
    """Write one self-describing ZIP whose manifest hashes exact payloads."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    archive = BACKUPS_DIR / f"brain-{stamp}.zip"

    records: list[dict[str, Any]] = []
    payloads: list[tuple[str, bytes]] = []
    with _corpus_lock():
        for path in _snapshot_files():
            relative = path.relative_to(BRAIN_ROOT).as_posix()
            data = path.read_bytes()
            payloads.append((relative, data))
            records.append({"path": relative, "sha256": _sha256(data), "size": len(data)})

    manifest = {
        "schema_version": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(records),
        "files": records,
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")

    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as zf:
        for relative, data in payloads:
            zf.writestr(relative, data)
        zf.writestr(MANIFEST_NAME, manifest_bytes)
    return archive


def _safe_member_name(name: str) -> str:
    if not name or "\\" in name or "\x00" in name:
        raise ValueError(f"Unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"Path traversal detected: {name}")
    normalized = path.as_posix()
    if path.parts[0] not in ALLOWED_TOP_LEVEL:
        raise ValueError(f"Archive member outside restore allowlist: {name}")
    return normalized


def _is_regular_zip_member(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return not info.is_dir() and stat.S_IFMT(mode) in (0, stat.S_IFREG)


def _read_and_validate_archive(archive: Path) -> dict[str, bytes]:
    """Validate names, types, member set, sizes, and SHA-256 hashes."""
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            if len(zf.infolist()) > MAX_BACKUP_FILES + 1:
                raise ValueError("Backup exceeds the member-count limit")
            declared_total = 0
            infos: dict[str, zipfile.ZipInfo] = {}
            for info in zf.infolist():
                name = _safe_member_name(info.filename)
                if name in infos:
                    raise ValueError(f"Duplicate archive member: {name}")
                if not _is_regular_zip_member(info):
                    raise ValueError(f"Unsupported archive member type: {name}")
                if info.flag_bits & 0x1:
                    raise ValueError(f"Encrypted archive members are unsupported: {name}")
                if info.file_size > MAX_MEMBER_SIZE:
                    raise ValueError(f"Archive member exceeds size limit: {name}")
                declared_total += info.file_size
                if declared_total > MAX_TOTAL_SIZE:
                    raise ValueError("Backup exceeds the total uncompressed-size limit")
                infos[name] = info

            if MANIFEST_NAME not in infos:
                raise ValueError("Backup manifest is missing")
            try:
                manifest = json.loads(zf.read(infos[MANIFEST_NAME]))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("Backup manifest is invalid") from exc
            if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_VERSION:
                raise ValueError("Unsupported backup manifest version")
            records = manifest.get("files")
            if not isinstance(records, list) or manifest.get("file_count") != len(records):
                raise ValueError("Backup manifest file count is invalid")

            expected: dict[str, dict[str, Any]] = {}
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("Backup manifest contains an invalid record")
                path = _safe_member_name(str(record.get("path", "")))
                if path == MANIFEST_NAME or path in expected:
                    raise ValueError(f"Duplicate manifest path: {path}")
                expected[path] = record
            if set(infos) - {MANIFEST_NAME} != set(expected):
                raise ValueError("Archive members do not match the backup manifest")

            payloads: dict[str, bytes] = {}
            for path, record in expected.items():
                data = zf.read(infos[path])
                if record.get("size") != len(data) or record.get("sha256") != _sha256(data):
                    raise ValueError(f"Backup integrity check failed: {path}")
                payloads[path] = data
            return payloads
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid zip archive: {archive}") from exc


def _write_staging(payloads: dict[str, bytes], staging: Path) -> None:
    for relative, data in payloads.items():
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(staging.resolve()):
            raise ValueError(f"Unsafe staged path: {relative}")
        with open(target, "xb") as stream:
            stream.write(data)


def _lint_staging(staging: Path) -> None:
    sys.path.insert(0, str(BRAIN_ROOT))
    from brain.runtime.lint import BrainLint

    errors = [finding for finding in BrainLint(staging).run() if finding.severity == "error"]
    if errors:
        detail = "; ".join(f"{f.code}: {f.message}" for f in errors[:5])
        raise ValueError(f"Staged backup failed brain_lint: {detail}")


def _managed_names() -> tuple[str, ...]:
    return (*SNAPSHOT_DIRS, *SNAPSHOT_FILES)


def _promote_with_rollback(staging: Path, rollback: Path) -> None:
    """Promote staged roots and restore the prior corpus on any failure."""
    rollback.mkdir()
    moved_old: list[str] = []
    promoted: list[str] = []
    try:
        for name in _managed_names():
            current = BRAIN_ROOT / name
            if current.exists():
                os.replace(current, rollback / name)
                moved_old.append(name)
        for name in _managed_names():
            staged = staging / name
            if staged.exists():
                os.replace(staged, BRAIN_ROOT / name)
                promoted.append(name)
    except Exception:
        for name in reversed(promoted):
            current = BRAIN_ROOT / name
            if current.is_dir():
                shutil.rmtree(current)
            elif current.exists():
                current.unlink()
        for name in reversed(moved_old):
            os.replace(rollback / name, BRAIN_ROOT / name)
        raise


def restore_backup(archive: Path) -> None:
    """Validate, lint, and restore a snapshot with automatic rollback."""
    with _corpus_lock():
        payloads = _read_and_validate_archive(archive.resolve())
        staging = Path(tempfile.mkdtemp(prefix=".restore-stage-", dir=BRAIN_ROOT))
        rollback = Path(tempfile.mkdtemp(prefix=".restore-rollback-", dir=BRAIN_ROOT))
        rollback.rmdir()
        try:
            _write_staging(payloads, staging)
            _lint_staging(staging)
            _promote_with_rollback(staging, rollback)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(rollback, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Super Brain — backup/restore")
    parser.add_argument("--restore", metavar="ZIP", help="Restore from a backup archive")
    parser.add_argument("--list", action="store_true", help="List existing backups")
    args = parser.parse_args()

    if args.list:
        if not BACKUPS_DIR.exists():
            print("no backups yet")
            return 0
        for archive in sorted(BACKUPS_DIR.glob("brain-*.zip")):
            print(f"  {archive.name}  ({archive.stat().st_size / 1024:.0f} KB)")
        return 0

    if args.restore:
        archive = Path(args.restore)
        if not archive.is_file():
            print(f"backup not found: {archive}", file=sys.stderr)
            return 1
        try:
            restore_backup(archive)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"restore failed: {exc}", file=sys.stderr)
            return 1
        print(f"restored validated backup: {archive.name}")
        return 0

    try:
        archive = create_backup()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"backup written: {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

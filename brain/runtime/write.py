"""
Super Brain — Write Subsystem

Deep module: Pi sees BrainWriter.ingest() and BrainWriter.write().
Pi does not see hashing, dedup, provenance, concurrency, atomicity, or logging.

Phase 2: immutable source capture + safe knowledge writes.
V1 hardening: file-based locking prevents concurrent writes
from silently overwriting each other.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Iterator, Literal, TypedDict, TypeAlias, cast

logger = logging.getLogger("brain.write")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_ROOT = Path(__file__).resolve().parent.parent.parent

KNOWLEDGE_ROOT = BRAIN_ROOT / "knowledge"

ALLOWED_KINDS = frozenset({"concept", "entity", "comparison", "timeline", "project"})

# Phase 6: exactly three epistemic states.
ALLOWED_STATUSES = frozenset({"provisional", "verified", "disputed"})

# Autonomous writes may only produce provisional knowledge. verified and
# disputed are review states — they change only via brain_review().
AUTONOMOUS_STATUS = frozenset({"provisional"})

# Source authority is categorical, not numeric (Phase 6).
ALLOWED_AUTHORITY = frozenset({"primary", "secondary", "unknown"})

REQUIRED_FRONTMATTER_FIELDS = frozenset({"title", "kind", "source_refs", "status"})

SUPPORTED_EXTENSIONS = frozenset({".md", ".txt", ".json", ".yaml", ".yml", ".pdf"})
SUPPORTED_SCHEMES = frozenset({"http", "https"})

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

ERROR_CODES = frozenset({
    "INVALID_REQUEST",
    "UNSAFE_PATH",
    "SOURCE_NOT_FOUND",
    "SOURCE_INTEGRITY_FAILED",
    "INVALID_FRONTMATTER",
    "DUPLICATE_TITLE",
    "TARGET_EXISTS",
    "TARGET_NOT_FOUND",
    "STALE_WRITE",
    "WRITE_FAILED",
    "VERIFIED_TARGET_REQUIRES_REVIEW",
    "MISSING_PROVENANCE",
    "PROVENANCE_REMOVED",
    "UNSUPPORTED_FORMAT",
})

# ---------------------------------------------------------------------------
# Discriminated unions — request types
# ---------------------------------------------------------------------------


class CreateKnowledge(TypedDict):
    op: Literal["create"]
    path: str
    content: str
    source_refs: list[str]


class UpdateKnowledge(TypedDict):
    op: Literal["update"]
    path: str
    content: str
    source_refs: list[str]
    expected_sha256: str


KnowledgeWrite: TypeAlias = CreateKnowledge | UpdateKnowledge

# ---------------------------------------------------------------------------
# Discriminated unions — ingest results
# ---------------------------------------------------------------------------


class IngestCreated(TypedDict):
    status: Literal["created"]
    source_id: str
    sha256: str
    path: str


class IngestExisting(TypedDict):
    status: Literal["existing"]
    source_id: str
    sha256: str
    path: str


IngestResult: TypeAlias = IngestCreated | IngestExisting

# ---------------------------------------------------------------------------
# Discriminated unions — write results
# ---------------------------------------------------------------------------


class WriteCreated(TypedDict):
    status: Literal["created"]
    path: str
    sha256: str


class WriteUpdated(TypedDict):
    status: Literal["updated"]
    path: str
    old_sha256: str
    new_sha256: str


class WriteRejected(TypedDict):
    status: Literal["rejected"]
    code: str
    message: str


WriteResult: TypeAlias = WriteCreated | WriteUpdated | WriteRejected

# ---------------------------------------------------------------------------
# Internal data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceMetadata:
    source_id: str
    sha256: str
    filename: str
    ingested_at: str
    project: str | None = None


@dataclass(frozen=True)
class WriteLogEntry:
    write_id: str
    timestamp: str
    operation: str
    path: str
    new_sha256: str | None = None
    old_sha256: str | None = None
    source_refs: list[str] | None = None
    status: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class IngestLogEntry:
    timestamp: str
    source_id: str
    sha256: str
    path: str
    operation: str  # "created" or "existing"


# ---------------------------------------------------------------------------
# BrainWriter — deep module
# ---------------------------------------------------------------------------


class BrainWriter:
    """Deep module owning all write mechanics behind a simple interface."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or BRAIN_ROOT
        self.knowledge_root = self.root / "knowledge"
        self.raw_sources = self.root / "raw" / "sources"
        self.logs_dir = self.root / "logs"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # Sentinel to distinguish "authority not provided" from "authority=unknown"
    _AUTHORITY_OMITTED = object()

    def ingest(
        self,
        source_path: str | Path,
        project: str | None = None,
        authority: str | object = _AUTHORITY_OMITTED,
    ) -> IngestResult:
        """Preserve a source under the shared mutation lock."""
        with self._mutation_lock():
            return self._ingest_locked(source_path, project, authority)

    def _ingest_locked(
        self,
        source_path: str | Path,
        project: str | None = None,
        authority: str | object = _AUTHORITY_OMITTED,
    ) -> IngestResult:
        """Preserve a source. Returns created or existing.

        authority is categorical metadata (primary | secondary | unknown)
        for Pi's review reasoning — never a numeric reputation score.

        When authority is omitted (default), existing authority classification
        is preserved for re-ingests. Only new sources or explicit reclassifications
        set the authority field.
        """
        path_str = str(source_path)

        # Check if this is a URL
        if path_str.startswith(("http://", "https://")):
            raw_bytes, filename = self._fetch_url(path_str)
        else:
            path = Path(path_str).resolve()

            # Validate extension
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                raise ValueError(f"UNSUPPORTED_FORMAT: {path.suffix}")

            # Read bytes once
            raw_bytes = path.read_bytes()
            filename = path.name

            # Convert PDF to markdown if needed
            if path.suffix.lower() == ".pdf":
                raw_bytes, filename = self._extract_pdf(raw_bytes, filename)

        # Hash and derive source ID
        sha = hashlib.sha256(raw_bytes).hexdigest()
        source_id = f"SRC-{sha[:12]}"

        source_dir = self.raw_sources / source_id

        # Check for existing source
        if source_dir.exists():
            meta_path = source_dir / "metadata.yaml"
            if meta_path.exists():
                meta = self._load_yaml(meta_path)
                existing_sha = meta.get("sha256", "")
                if existing_sha == sha:
                    # Existing source: only update authority if explicitly
                    # provided. This prevents unintentional downgrades when
                    # re-ingesting without specifying authority.
                    if authority is not self._AUTHORITY_OMITTED:
                        if authority not in ALLOWED_AUTHORITY:
                            raise ValueError(f"INVALID_AUTHORITY: {authority}. Allowed: {sorted(ALLOWED_AUTHORITY)}")
                        if meta.get("authority") != authority:
                            # Atomic metadata update
                            self._save_yaml_atomic(meta_path, {**meta, "authority": authority})
                    return IngestExisting(
                        status="existing",
                        source_id=source_id,
                        sha256=sha,
                        path=str(source_dir / "source.md"),
                    )

        # Create source directory
        source_dir.mkdir(parents=True, exist_ok=True)

        # Atomic copy source
        dest = source_dir / "source.md"
        with tempfile.NamedTemporaryFile(
            dir=str(source_dir), suffix=".tmp", delete=False
        ) as tmp:
            tmp.write(raw_bytes)
            tmp_path = tmp.name
        os.replace(tmp_path, str(dest))

        # Write metadata
        now = datetime.now(timezone.utc).isoformat()
        # For new sources, default authority to "unknown" if not specified
        if authority is self._AUTHORITY_OMITTED:
            effective_authority = "unknown"
        else:
            if authority not in ALLOWED_AUTHORITY:
                raise ValueError(f"INVALID_AUTHORITY: {authority}. Allowed: {sorted(ALLOWED_AUTHORITY)}")
            effective_authority = authority
        metadata = {
            "source_id": source_id,
            "sha256": sha,
            "filename": filename,
            "ingested_at": now,
            "project": project,
            "authority": effective_authority,
        }
        self._save_yaml(source_dir / "metadata.yaml", metadata)

        # Log ingestion
        ingest_entry = IngestLogEntry(
            timestamp=now,
            source_id=source_id,
            sha256=sha,
            path=str(dest),
            operation="created",
        )
        self._append_ingestion_log(ingest_entry)

        logger.info("Ingested: %s -> %s", source_id, dest)

        return IngestCreated(
            status="created",
            source_id=source_id,
            sha256=sha,
            path=str(dest),
        )

    def write(
        self,
        request: KnowledgeWrite,
    ) -> WriteResult:
        """Safely write one knowledge document. One write = one document."""
        op = request["op"]
        target_path = self.root / request["path"]

        # Validate path is under knowledge/
        try:
            self._validate_path(target_path)
        except ValueError as e:
            return self._reject(op, request["path"], "UNSAFE_PATH", str(e))

        # Parse and validate frontmatter from content
        frontmatter, body = self._parse_frontmatter(request["content"])
        try:
            self._validate_frontmatter(frontmatter)
        except ValueError as e:
            return self._reject(op, request["path"], "INVALID_FRONTMATTER", str(e))

        # Validate status rules
        status = frontmatter["status"]
        if status not in ALLOWED_STATUSES:
            return self._reject(
                op, request["path"], "INVALID_FRONTMATTER",
                f"Unknown status: {status}",
            )

        # Autonomous creates may only write provisional knowledge
        if op == "create" and status not in AUTONOMOUS_STATUS:
            return self._reject(
                op, request["path"], "INVALID_FRONTMATTER",
                (
                    f"Autonomous creates may only use status={AUTONOMOUS_STATUS}, "
                    f"got '{status}'. Verified knowledge requires owner review."
                ),
            )

        # Validate source refs
        source_refs = frontmatter["source_refs"]
        if not source_refs:
            return self._reject(op, request["path"], "MISSING_PROVENANCE", "No source references")

        # Verify source integrity
        for ref in source_refs:
            try:
                self._verify_source_integrity(ref)
            except ValueError as e:
                code = "SOURCE_NOT_FOUND" if "SOURCE_NOT_FOUND" in str(e) else "SOURCE_INTEGRITY_FAILED"
                return self._reject(op, request["path"], code, str(e))

        if op == "create":
            create_req: CreateKnowledge = request  # type: ignore[assignment]
            result = self._do_create(create_req, frontmatter, source_refs, body)
        elif op == "update":
            update_req: UpdateKnowledge = request  # type: ignore[assignment]
            result = self._do_update(update_req, frontmatter, source_refs, body)
        else:
            result = self._reject(op, request["path"], "INVALID_REQUEST", f"Unknown operation: {op}")

        # Log rejections (accepted writes log inside the atomic path)
        if result.get("status") == "rejected":
            self._log_rejected(op, request["path"], cast(WriteRejected, result)["code"])
        return result

    def _reject(self, op: str, path: str, code: str, message: str) -> WriteRejected:
        """Build a rejection result and log it."""
        self._log_rejected(op, path, code)
        return WriteRejected(status="rejected", code=code, message=message)

    # ------------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------------

    def _do_create(
        self,
        request: CreateKnowledge,
        frontmatter: dict,
        source_refs: list[str],
        body: str,
    ) -> WriteResult:
        target = self.root / request["path"]
        with self._mutation_lock():
            # These checks must happen while holding the same lock as the
            # replacement; otherwise two creators can both pass validation.
            if target.exists():
                return WriteRejected(
                    status="rejected",
                    code="TARGET_EXISTS",
                    message=f"Target already exists: {target}",
                )

            title = frontmatter["title"]
            normalized = " ".join(title.lower().split())
            for existing in self._iter_knowledge_files():
                existing_fm = self._load_frontmatter(existing)
                if existing_fm:
                    existing_norm = " ".join(existing_fm.get("title", "").lower().split())
                    if existing_norm == normalized:
                        return WriteRejected(
                            status="rejected",
                            code="DUPLICATE_TITLE",
                            message=f"Duplicate normalized title: {title}",
                        )

            content = self._build_content(frontmatter, body)
            return self._atomic_write(target, content, request["source_refs"])

    # ------------------------------------------------------------------
    # UPDATE
    # ------------------------------------------------------------------

    def _do_update(
        self,
        request: UpdateKnowledge,
        frontmatter: dict,
        source_refs: list[str],
        body: str,
    ) -> WriteResult:
        target = self.root / request["path"]
        with self._mutation_lock():
            # Re-read and validate while holding the mutation lock. This is
            # the compare-and-swap boundary; no writer/reviewer can change the
            # page between this hash check and os.replace().
            if not target.exists():
                return WriteRejected(
                    status="rejected",
                    code="TARGET_NOT_FOUND",
                    message=f"Target not found: {target}",
                )

            current_sha = hashlib.sha256(target.read_bytes()).hexdigest()
            if current_sha != request["expected_sha256"]:
                return WriteRejected(
                    status="rejected",
                    code="STALE_WRITE",
                    message="The knowledge file changed after it was read.",
                )

            existing_fm = self._load_frontmatter(target)
            existing_status = existing_fm.get("status", "provisional") if existing_fm else "provisional"

            if existing_status == "verified":
                return WriteRejected(
                    status="rejected",
                    code="VERIFIED_TARGET_REQUIRES_REVIEW",
                    message=(
                        "Verified knowledge is content-locked. Dispute it via "
                        "brain_review before updating."
                    ),
                )

            if existing_status == "disputed":
                frontmatter["status"] = "provisional"
            elif frontmatter.get("status") != "provisional":
                return WriteRejected(
                    status="rejected",
                    code="INVALID_FRONTMATTER",
                    message=(
                        f"Autonomous updates may only result in status=provisional, "
                        f"got '{frontmatter.get('status')}'. Use brain_review to "
                        "change epistemic state."
                    ),
                )

            old_refs = set(existing_fm.get("source_refs", []) if existing_fm else [])
            new_refs_set = set(source_refs)
            if not old_refs.issubset(new_refs_set):
                removed = old_refs - new_refs_set
                return WriteRejected(
                    status="rejected",
                    code="PROVENANCE_REMOVED",
                    message=f"Old provenance removed: {removed}",
                )

            merged_refs = sorted(old_refs | new_refs_set)
            frontmatter["source_refs"] = merged_refs
            content = self._build_content(frontmatter, body)
            return self._atomic_replace(target, content, request["expected_sha256"], merged_refs)

    # ------------------------------------------------------------------
    # Concurrency: file-based locking (v1 hardening)
    # ------------------------------------------------------------------

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        """Serialize knowledge mutations across processes and threads.

        The lock covers the complete read/validate/write transaction. A
        single corpus lock also makes duplicate-title checks atomic across
        creates targeting different paths. The handle is local to this
        context, so sharing a BrainWriter instance between threads cannot
        corrupt lock ownership.
        """
        lock_dir = self.root / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "knowledge-mutations.lock"
        try:
            lock_file = open(lock_path, "a+", encoding="utf-8")
        except OSError as exc:
            raise ValueError(
                f"WRITE_FAILED: cannot open mutation lock {lock_path}: {exc}"
            ) from exc
        with lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_path(self, path: Path) -> None:
        """Ensure path is under knowledge/, no traversal, no symlinks escaping."""
        try:
            path.relative_to(self.knowledge_root)
        except ValueError:
            raise ValueError(f"UNSAFE_PATH: {path} is outside knowledge/")

        # Check for path traversal
        if ".." in path.parts:
            raise ValueError(f"UNSAFE_PATH: traversal in {path}")

        # Resolve symlinks (target + parents); must still land inside knowledge/
        resolved = path.resolve()
        try:
            resolved.relative_to(self.knowledge_root)
        except ValueError:
            raise ValueError(f"UNSAFE_PATH: symlink escapes knowledge/ at {path}")

    def _parse_frontmatter(self, content: str) -> tuple[dict, str]:
        """Parse YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return {}, content

        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content

        fm_text = parts[1].strip()
        body = parts[2].strip()

        fm = self._load_yaml_string(fm_text)
        return fm, body

    def _load_frontmatter(self, path: Path) -> dict | None:
        """Load frontmatter from a file, return None if no valid frontmatter."""
        try:
            content = path.read_text(encoding="utf-8")
            fm, _ = self._parse_frontmatter(content)
            return fm if fm else None
        except Exception:
            return None

    def _validate_frontmatter(self, fm: dict) -> None:
        """Validate required frontmatter fields."""
        missing = REQUIRED_FRONTMATTER_FIELDS - set(fm.keys())
        if missing:
            raise ValueError(f"INVALID_FRONTMATTER: missing fields: {missing}")

        kind = fm.get("kind")
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"INVALID_FRONTMATTER: unknown kind: {kind}")

        status = fm.get("status")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"INVALID_FRONTMATTER: unknown status: {status}")

        source_refs = fm.get("source_refs")
        if not isinstance(source_refs, list):
            raise ValueError("INVALID_FRONTMATTER: source_refs must be a list")

    def _verify_source_integrity(self, source_ref: str) -> None:
        """Verify a source reference exists and its hash matches.

        Triple-check: actual source SHA, metadata SHA, and SRC-* hash
        prefix must all agree. If both source.md and metadata.yaml are
        changed together, this still catches the tampering because the
        SRC-* prefix is derived from the actual content hash.
        """
        if not re.fullmatch(r"SRC-[0-9a-f]{12}", source_ref):
            raise ValueError(f"SOURCE_NOT_FOUND: invalid source ID format: {source_ref}")

        source_id = source_ref
        meta_path = self.raw_sources / source_id / "metadata.yaml"

        if not meta_path.exists():
            raise ValueError(f"SOURCE_NOT_FOUND: {source_ref}")

        meta = self._load_yaml(meta_path)
        expected_sha = meta.get("sha256", "")

        source_file = self.raw_sources / source_id / "source.md"
        if not source_file.is_file():
            raise ValueError(f"SOURCE_INTEGRITY_FAILED: {source_ref} (source.md missing)")

        actual_sha = hashlib.sha256(source_file.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            raise ValueError(f"SOURCE_INTEGRITY_FAILED: {source_ref} (source/metadata SHA mismatch)")

        expected_source_id = f"SRC-{actual_sha[:12]}"
        if source_id != expected_source_id:
            raise ValueError(
                f"SOURCE_INTEGRITY_FAILED: {source_ref} "
                f"(source_id prefix mismatch: expected {expected_source_id})"
            )

    def _atomic_write(
        self,
        target: Path,
        content: str,
        source_refs: list[str],
    ) -> WriteResult:
        """Write a new file atomically and return WriteCreated."""
        target.parent.mkdir(parents=True, exist_ok=True)

        # Caller holds _mutation_lock() across validation and replacement.
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=str(target.parent), suffix=".tmp", delete=False, mode="w"
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            try:
                os.replace(tmp_path, str(target))
            except Exception:
                os.unlink(tmp_path)
                return WriteRejected(
                    status="rejected",
                    code="WRITE_FAILED",
                    message=f"Failed to write: {target}",
                )

            sha = hashlib.sha256(target.read_bytes()).hexdigest()
            self._append_write_log(WriteLogEntry(
                write_id=self._gen_write_id(),
                timestamp=datetime.now(timezone.utc).isoformat(),
                operation="create",
                path=str(target),
                new_sha256=sha,
                source_refs=source_refs,
                status="created",
            ))
            return WriteCreated(status="created", path=self._rel(target), sha256=sha)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _atomic_replace(
        self,
        target: Path,
        content: str,
        expected_sha: str,
        source_refs: list[str],
    ) -> WriteResult:
        """Replace file atomically with optimistic concurrency check. Return WriteUpdated."""
        # Caller holds _mutation_lock() across validation and replacement.
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=str(target.parent), suffix=".tmp", delete=False, mode="w"
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            try:
                os.replace(tmp_path, str(target))
            except Exception:
                os.unlink(tmp_path)
                return WriteRejected(
                    status="rejected",
                    code="WRITE_FAILED",
                    message=f"Failed to replace: {target}",
                )

            new_sha = hashlib.sha256(target.read_bytes()).hexdigest()
            self._append_write_log(WriteLogEntry(
                write_id=self._gen_write_id(),
                timestamp=datetime.now(timezone.utc).isoformat(),
                operation="update",
                path=str(target),
                old_sha256=expected_sha,
                new_sha256=new_sha,
                source_refs=source_refs,
                status="updated",
            ))
            return WriteUpdated(
                status="updated",
                path=self._rel(target),
                old_sha256=expected_sha,
                new_sha256=new_sha,
            )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _build_content(self, frontmatter: dict, body: str) -> str:
        """Reconstruct markdown with frontmatter."""
        fm_lines = ["---\n"]
        for key, value in frontmatter.items():
            if isinstance(value, list):
                fm_lines.append(f"{key}:\n")
                for item in value:
                    fm_lines.append(f"  - {item}\n")
            else:
                fm_lines.append(f"{key}: {value}\n")
        fm_lines.append("---\n")
        return "".join(fm_lines) + body

    def _iter_knowledge_files(self) -> list[Path]:
        """Iterate all .md files under knowledge/."""
        if not self.knowledge_root.exists():
            return []
        return sorted(self.knowledge_root.rglob("*.md"))

    def _load_yaml(self, path: Path) -> dict:
        """Load a YAML file as dict."""
        try:
            import yaml
        except ImportError:
            # Minimal YAML parser for simple key-value pairs
            return self._parse_simple_yaml(path.read_text())
        text = path.read_text(encoding="utf-8")
        return self._load_yaml_string(text)

    def _load_yaml_string(self, text: str) -> dict:
        """Parse YAML string to dict."""
        try:
            import yaml
            result = yaml.safe_load(text)
            return result if isinstance(result, dict) else {}
        except ImportError:
            return self._parse_simple_yaml(text)

    def _save_yaml(self, path: Path, data: dict) -> None:
        """Save dict as YAML."""
        try:
            import yaml
            path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")
        except ImportError:
            path.write_text(self._dict_to_simple_yaml(data), encoding="utf-8")

    def _save_yaml_atomic(self, path: Path, data: dict) -> None:
        """Save dict as YAML atomically (temp file + os.replace)."""
        tmp_path = path.with_suffix(".tmp")
        try:
            self._save_yaml(tmp_path, data)
            os.replace(str(tmp_path), str(path))
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _parse_simple_yaml(self, text: str) -> dict:
        """Minimal YAML parser for simple key-value and list structures."""
        result: dict = {}
        current_key: str | None = None
        current_list: list[str] | None = None

        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if stripped.startswith("- "):
                if current_key and current_list is not None:
                    current_list.append(stripped[2:].strip())
                continue

            if ":" in stripped and not stripped.startswith(" "):
                if current_key and current_list is not None:
                    result[current_key] = current_list
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()
                current_key = key
                if value:
                    result[key] = value
                    current_list = None
                else:
                    current_list = []
            elif current_key and current_list is not None:
                current_list.append(stripped)

        if current_key and current_list is not None:
            result[current_key] = current_list

        return result

    def _dict_to_simple_yaml(self, data: dict) -> str:
        """Convert dict to simple YAML string."""
        lines = []
        for key, value in data.items():
            if isinstance(value, list):
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines) + "\n"

    def _log_rejected(self, op: str, path: str, code: str) -> None:
        """Log a rejected write (blueprint point 34)."""
        self._append_write_log(WriteLogEntry(
            write_id=self._gen_write_id(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            operation=op,
            path=path,
            status="rejected",
            error=code,
        ))

    def _append_ingestion_log(self, entry: IngestLogEntry) -> None:
        """Append an ingestion log entry."""
        try:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.logs_dir / "ingestion.jsonl"
            log_entry = {
                "timestamp": entry.timestamp,
                "source_id": entry.source_id,
                "sha256": entry.sha256,
                "path": entry.path,
                "operation": entry.operation,
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
        except OSError:
            logger.exception("Failed to append ingestion log")

    def _append_write_log(self, entry: WriteLogEntry) -> None:
        """Append a write log entry."""
        try:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.logs_dir / "writes.jsonl"
            log_entry = {
                "write_id": entry.write_id,
                "timestamp": entry.timestamp,
                "operation": entry.operation,
                "path": entry.path,
                "new_sha256": entry.new_sha256,
                "old_sha256": entry.old_sha256,
                "source_refs": entry.source_refs,
                "status": entry.status,
                "error": entry.error,
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
        except OSError:
            logger.exception("Failed to append write log")

    def _gen_write_id(self) -> str:
        """Generate a short write ID."""
        import secrets
        return f"BW-{secrets.token_hex(3).upper()}"

    def _rel(self, path: Path) -> str:
        """Path relative to brain root (repo-relative, e.g. knowledge/concepts/rag.md)."""
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    # ------------------------------------------------------------------
    # URL and PDF support
    # ------------------------------------------------------------------

    def _fetch_url(self, url: str) -> tuple[bytes, str]:
        """Fetch URL content and return (bytes, filename).

        Uses urllib.request (stdlib) for HTTP/HTTPS URLs. SSRF-guarded:
        only http/https is allowed, the hostname is resolved and rejected
        when it lands in a private/loopback/link-local/reserved range, and
        redirects are refused so an external URL cannot bounce onto an
        internal endpoint.
        """
        import urllib.request
        import urllib.error
        import urllib.parse

        parsed = urllib.parse.urlparse(url)

        # Only http/https. Blocks file://, gopher://, ftp://, data: etc.
        if parsed.scheme not in SUPPORTED_SCHEMES:
            raise ValueError(f"URL_FETCH_FAILED: disallowed scheme {parsed.scheme!r}")
        if not parsed.hostname:
            raise ValueError(f"URL_FETCH_FAILED: no hostname in {url}")

        # SSRF guard: resolve the host and refuse any non-public address.
        self._assert_public_host(parsed.hostname)

        # Refuse redirects: a public URL must not be able to 3xx onto an
        # internal address (redirect-based SSRF bypass).
        opener = urllib.request.build_opener(_NoRedirect())

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "SuperBrain/1.0 (knowledge ingestion)"
            })
            with opener.open(req, timeout=30) as response:
                content_type = response.headers.get("Content-Type", "")
                data = response.read()

                # Determine filename from the URL path.
                filename = parsed.path.split("/")[-1] or "index.html"

                # Clean up filename
                if not filename or filename == "index.html":
                    filename = "page.html"

                # If content is HTML, convert to markdown
                if "text/html" in content_type:
                    return self._html_to_markdown(data, filename, url), "page.md"

                return data, filename

        except urllib.error.HTTPError as e:
            raise ValueError(f"URL_FETCH_FAILED: {url} - {e}")
        except urllib.error.URLError as e:
            raise ValueError(f"URL_FETCH_FAILED: {url} - {e.reason}")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"URL_FETCH_FAILED: {url} - {e}")

    def _assert_public_host(self, host: str) -> None:
        """Reject private/loopback/link-local/reserved hosts (SSRF guard).

        Resolves every A/AAAA record and refuses the fetch if any lands in a
        non-public range. This is a resolve-then-fetch check, so a malicious
        host could DNS-rebind between here and the socket connect; pin the
        resolved IP (rebuild the Host header + SNI) if that threat matters.
        """
        import ipaddress
        import socket

        try:
            addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
        except socket.gaierror:
            addrs = {host}

        for raw in addrs:
            try:
                ip = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                raise ValueError(
                    f"URL_FETCH_FAILED: host {host!r} resolves to a non-public "
                    f"address {raw}; refusing to fetch."
                )

    def _html_to_markdown(self, html_bytes: bytes, filename: str, source_url: str = "") -> bytes:
        """Convert HTML to markdown (basic implementation).

        Uses stdlib only; for production use, consider beautifulsoup4 + markdownify.
        """
        import re

        try:
            html = html_bytes.decode("utf-8", errors="replace")
        except Exception:
            return html_bytes

        # Extract title
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else filename

        # Extract body content (simplified)
        body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.IGNORECASE | re.DOTALL)
        if body_match:
            body = body_match.group(1)
        else:
            body = html

        # Basic HTML to markdown conversion
        # Remove scripts and styles
        body = re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.IGNORECASE | re.DOTALL)
        body = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.IGNORECASE | re.DOTALL)

        # Convert headings
        for i in range(1, 7):
            body = re.sub(
                rf"<h{i}[^>]*>(.*?)</h{i}>",
                f"{'#' * i} \1\n\n",
                body,
                flags=re.IGNORECASE | re.DOTALL,
            )

        # Convert paragraphs
        body = re.sub(r"<p[^>]*>(.*?)</p>", "\1\n\n", body, flags=re.IGNORECASE | re.DOTALL)

        # Convert line breaks
        body = re.sub(r"<br[^>]*>", "\n", body, flags=re.IGNORECASE)

        # Convert links
        body = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", body, flags=re.IGNORECASE | re.DOTALL)

        # Convert bold
        body = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", body, flags=re.IGNORECASE | re.DOTALL)
        body = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", body, flags=re.IGNORECASE | re.DOTALL)

        # Convert italic
        body = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", body, flags=re.IGNORECASE | re.DOTALL)
        body = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", body, flags=re.IGNORECASE | re.DOTALL)

        # Convert code
        body = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", body, flags=re.IGNORECASE | re.DOTALL)

        # Convert lists
        body = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", body, flags=re.IGNORECASE | re.DOTALL)

        # Remove remaining HTML tags
        body = re.sub(r"<[^>]+>", "", body)

        # Clean up whitespace
        body = re.sub(r"\n{3,}", "\n\n", body)
        body = re.sub(r"[ \t]+", " ", body)
        body = body.strip()

        # Build markdown
        url_field = f"source_url: \"{source_url}\"" if source_url else "source_url: \"\""
        markdown = f"---\n{url_field}\ntitle: \"{title}\"\nretrieved_at: \"{datetime.now(timezone.utc).isoformat()}\"\nauthority: secondary\n---\n\n{body}\n"

        return markdown.encode("utf-8")

    def _extract_pdf(self, pdf_bytes: bytes, filename: str) -> tuple[bytes, str]:
        """Extract text from PDF and return (markdown_bytes, filename).

        Tries pypdf first, then pdfminer, then falls back to OCR via pdftoppm + tesseract.
        """
        # Try pypdf
        try:
            from pypdf import PdfReader  # type: ignore[reportMissingImports]
            reader = PdfReader(BytesIO(pdf_bytes))
            text_parts = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)

            if text_parts:
                markdown = (
                    f"---\nsource_file: \"{filename}\"\n"
                    f"pages: {len(reader.pages)}\n"
                    f"retrieved_at: \"{datetime.now(timezone.utc).isoformat()}\"\n"
                    f"authority: primary\n---\n\n"
                    + "\n\n---\n\n".join(text_parts)
                    + "\n"
                )
                return markdown.encode("utf-8"), "extracted.md"

        except ImportError:
            pass
        except Exception as e:
            logger.warning("pypdf extraction failed: %s", e)

        # Try pdfminer
        try:
            from pdfminer.high_level import extract_text  # type: ignore[reportMissingImports]
            from pdfminer.layout import LAParams  # type: ignore[reportMissingImports]

            text = extract_text(BytesIO(pdf_bytes), laparams=LAParams())
            if text:
                markdown = (
                    f"---\nsource_file: \"{filename}\"\n"
                    f"retrieved_at: \"{datetime.now(timezone.utc).isoformat()}\"\n"
                    f"authority: primary\n---\n\n{text}\n"
                )
                return markdown.encode("utf-8"), "extracted.md"

        except ImportError:
            pass
        except Exception as e:
            logger.warning("pdfminer extraction failed: %s", e)

        # Try OCR via pdftoppm + tesseract (for scanned PDFs)
        try:
            import subprocess
            import tempfile
            import os

            with tempfile.TemporaryDirectory() as tmpdir:
                # Convert PDF pages to images
                pdf_path = os.path.join(tmpdir, "input.pdf")
                with open(pdf_path, "wb") as f:
                    f.write(pdf_bytes)

                # Use pdftoppm to convert to PNG
                img_prefix = os.path.join(tmpdir, "page")
                result = subprocess.run(
                    ["pdftoppm", "-png", "-r", "300", pdf_path, img_prefix],
                    capture_output=True,
                    text=True,
                )

                if result.returncode != 0:
                    logger.warning("pdftoppm failed: %s", result.stderr)
                    raise ValueError(
                        "PDF_EXTRACTION_FAILED: pdftoppm failed. "
                        "Install poppler-utils (apt install poppler-utils)."
                    )

                # OCR each page image
                # pdftoppm names files as prefix-001.png (zero-padded, 3 digits)
                ocr_parts = []
                page_num = 1
                while True:
                    img_path = f"{img_prefix}-{page_num:03d}.png"
                    if not os.path.exists(img_path):
                        break

                    # Run tesseract OCR
                    ocr_result = subprocess.run(
                        ["tesseract", img_path, "stdout", "--psm", "6"],
                        capture_output=True,
                        text=True,
                    )

                    if ocr_result.returncode == 0 and ocr_result.stdout.strip():
                        ocr_parts.append(f"## Page {page_num}\n\n{ocr_result.stdout.strip()}")

                    page_num += 1

                if ocr_parts:
                    markdown = (
                        f"---\nsource_file: \"{filename}\"\n"
                        f"method: ocr\n"
                        f"pages: {len(ocr_parts)}\n"
                        f"retrieved_at: \"{datetime.now(timezone.utc).isoformat()}\"\n"
                        f"authority: primary\n---\n\n"
                        + "\n\n".join(ocr_parts)
                        + "\n"
                    )
                    return markdown.encode("utf-8"), "ocr_extracted.md"

        except (ImportError, FileNotFoundError) as e:
            logger.warning("OCR extraction failed: %s", e)
        except Exception as e:
            logger.warning("OCR extraction failed: %s", e)

        # No extraction method worked
        raise ValueError(
            "PDF_EXTRACTION_FAILED: No PDF library or OCR tool available. "
            "Install pypdf (pip install pypdf), pdfminer.six, or poppler-utils + tesseract."
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so an external URL cannot bounce to an internal host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise urllib.error.HTTPError(newurl, code, "Redirect refused", headers, None)

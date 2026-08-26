"""
Super Brain — Experience Memory Subsystem

Deep module: Pi sees BrainMemory.remember(). Pi does not see the machinery.

Phase 4: append-only experience memory for decisions and lessons.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path
from typing import Literal, TypedDict, TypeAlias, cast

logger = logging.getLogger("brain.remember")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_ROOT = Path(__file__).resolve().parent.parent.parent

HISTORY_ROOT = BRAIN_ROOT / "history"

DECISIONS_ROOT = HISTORY_ROOT / "decisions"

LESSONS_ROOT = HISTORY_ROOT / "lessons"

LOGS_DIR = BRAIN_ROOT / "logs"

MEMORY_LOG = LOGS_DIR / "memory.jsonl"

ALLOWED_PROJECTS = frozenset({
    "super-brain",
    "pi",
    "context-mode",
    "pi-lens",
})

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

ERROR_CODES = frozenset({
    "INVALID_REQUEST",
    "INVALID_KIND",
    "INVALID_PROJECT",
    "MISSING_FIELD",
    "DUPLICATE_MEMORY",
    "INVALID_CONTEXT_REF",
    "WRITE_FAILED",
    "UNSAFE_PATH",
})

# ---------------------------------------------------------------------------
# Discriminated unions — request types
# ---------------------------------------------------------------------------


class RememberDecision(TypedDict):
    """Request to remember a project decision."""
    kind: Literal["decision"]
    project: str
    title: str
    decision: str
    rationale: str
    alternatives: list[str]
    context_refs: list[str]


class RememberLesson(TypedDict):
    """Request to remember a lesson from experience."""
    kind: Literal["lesson"]
    project: str
    title: str
    lesson: str
    learned_from: str
    context_refs: list[str]


MemoryRequest: TypeAlias = RememberDecision | RememberLesson

# ---------------------------------------------------------------------------
# Discriminated unions — result types
# ---------------------------------------------------------------------------


class MemoryCreated(TypedDict):
    """Memory successfully created."""
    status: Literal["created"]
    id: str
    path: str
    sha256: str
    fingerprint: str


class MemoryRejected(TypedDict):
    """Memory request rejected."""
    status: Literal["rejected"]
    code: str
    message: str


MemoryResult: TypeAlias = MemoryCreated | MemoryRejected

# ---------------------------------------------------------------------------
# Internal data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryLogEntry:
    """Log entry for memory operations."""
    memory_id: str
    timestamp: str
    kind: str
    project: str
    path: str
    sha256: str
    status: str
    error: str | None = None


# ---------------------------------------------------------------------------
# BrainMemory — deep module
# ---------------------------------------------------------------------------


class BrainMemory:
    """Deep module owning all experience-memory mechanics behind a simple interface."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or BRAIN_ROOT
        self.decisions_root = self.root / "history" / "decisions"
        self.lessons_root = self.root / "history" / "lessons"
        self.logs_dir = self.root / "logs"
        # Ensure directories exist
        self.decisions_root.mkdir(parents=True, exist_ok=True)
        self.lessons_root.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def remember(self, request: MemoryRequest) -> MemoryResult:
        """
        Remember a decision or lesson.

        Args:
            request: Either RememberDecision or RememberLesson.

        Returns:
            MemoryCreated or MemoryRejected.
        """
        kind = request.get("kind")

        if kind not in ("decision", "lesson"):
            return MemoryRejected(
                status="rejected",
                code="INVALID_KIND",
                message=f"Unknown kind: {kind}. Must be 'decision' or 'lesson'.",
            )

        project = request.get("project", "")
        if project not in ALLOWED_PROJECTS:
            return MemoryRejected(
                status="rejected",
                code="INVALID_PROJECT",
                message=f"Unknown project: {project}.",
            )

        # Validate required fields based on kind
        if kind == "decision":
            result = self._validate_decision(request)  # type: ignore[arg-type]
        else:
            result = self._validate_lesson(request)  # type: ignore[arg-type]

        if result is not None:
            return result

        # Generate ID and path
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y%m%d")
        random_suffix = hashlib.sha256(now.isoformat().encode()).hexdigest()[:4]
        memory_id = f"{kind.upper()[:3]}-{date_str}-{random_suffix}"

        if kind == "decision":
            target_dir = self.decisions_root
        else:
            target_dir = self.lessons_root

        # Sanitize title for filename
        safe_title = re.sub(r"[^a-zA-Z0-9]+", "-", request["title"]).strip("-").lower()
        if not safe_title:
            safe_title = "untitled"

        path = target_dir / f"{memory_id}-{safe_title}.md"

        # Check for duplicate
        fingerprint = self._compute_fingerprint(request)
        if self._is_duplicate(fingerprint):
            return MemoryRejected(
                status="rejected",
                code="DUPLICATE_MEMORY",
                message=f"Duplicate {kind}: fingerprint {fingerprint[:8]}...",
            )

        # Generate content
        content = self._generate_content(request, memory_id, project, now)

        # Atomic write
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=str(target_dir), suffix=".tmp", delete=False
            ) as tmp:
                tmp.write(content.encode("utf-8"))
                tmp_path = tmp.name
            os.replace(tmp_path, str(path))
        except OSError as e:
            logger.error(f"Write failed: {e}")
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return MemoryRejected(
                status="rejected",
                code="WRITE_FAILED",
                message=str(e),
            )

        # Compute SHA
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Log
        self._log_memory(memory_id, kind, project, str(path), sha, "created")

        return MemoryCreated(
            status="created",
            id=memory_id,
            path=str(path),
            sha256=sha,
            fingerprint=fingerprint,
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_decision(self, request: RememberDecision) -> MemoryRejected | None:
        """Validate a decision request."""
        required = ["title", "decision", "rationale"]
        for field in required:
            if not request.get(field):
                return MemoryRejected(
                    status="rejected",
                    code="MISSING_FIELD",
                    message=f"Decision missing required field: {field}",
                )

        if not request.get("alternatives"):
            return MemoryRejected(
                status="rejected",
                code="MISSING_FIELD",
                message="Decision missing required field: alternatives",
            )

        return None

    def _validate_lesson(self, request: RememberLesson) -> MemoryRejected | None:
        """Validate a lesson request."""
        required = ["title", "lesson", "learned_from"]
        for field in required:
            if not request.get(field):
                return MemoryRejected(
                    status="rejected",
                    code="MISSING_FIELD",
                    message=f"Lesson missing required field: {field}",
                )

        return None

    # ------------------------------------------------------------------
    # Fingerprinting and deduplication
    # ------------------------------------------------------------------

    def _compute_fingerprint(self, request: MemoryRequest) -> str:
        """Compute fingerprint for duplicate detection."""
        kind = request["kind"]
        project = request["project"]
        title = request["title"]

        if kind == "decision":
            decision_req = cast(RememberDecision, request)
            main_content = decision_req["decision"]
        else:
            lesson_req = cast(RememberLesson, request)
            main_content = lesson_req["lesson"]

        fingerprint_data = f"{kind}:{project}:{title}:{main_content}"
        return hashlib.sha256(fingerprint_data.encode()).hexdigest()

    def _is_duplicate(self, fingerprint: str) -> bool:
        """Check if a memory with this fingerprint already exists."""
        # Search all existing memories for matching fingerprint
        all_files = list(chain(
            self.decisions_root.glob("*.md"),
            self.lessons_root.glob("*.md"),
        ))
        for memory_file in all_files:
            try:
                content = memory_file.read_text()
                if f"fingerprint: {fingerprint}" in content:
                    return True
            except (OSError, UnicodeDecodeError):
                continue
        return False

    # ------------------------------------------------------------------
    # Content generation
    # ------------------------------------------------------------------

    def _generate_content(
        self,
        request: MemoryRequest,
        memory_id: str,
        project: str,
        now: datetime,
    ) -> str:
        """Generate markdown content for the memory."""
        kind = request["kind"]
        title = request["title"]

        if kind == "decision":
            return self._generate_decision_content(
                request,  # type: ignore[arg-type]
                memory_id, project, title, now
            )
        else:
            return self._generate_lesson_content(
                request,  # type: ignore[arg-type]
                memory_id, project, title, now
            )

    def _generate_decision_content(
        self,
        request: RememberDecision,
        memory_id: str,
        project: str,
        title: str,
        now: datetime,
    ) -> str:
        """Generate decision markdown."""
        alternatives = "\n".join(f"- {alt}" for alt in request["alternatives"])
        context_refs = self._format_context_refs(request.get("context_refs", []))

        return f"""---
id: {memory_id}
kind: decision
project: {project}
date: {now.isoformat()}
status: active
tags: []
fingerprint: {self._compute_fingerprint(request)}
context_refs:
{context_refs}
---

# {title}

## Decision

{request['decision']}

## Rationale

{request['rationale']}

## Alternatives Considered

{alternatives}

## Revisit When

Real retrieval failures show lexical mismatch or ranking degradation.
"""

    def _generate_lesson_content(
        self,
        request: RememberLesson,
        memory_id: str,
        project: str,
        title: str,
        now: datetime,
    ) -> str:
        """Generate lesson markdown."""
        context_refs = self._format_context_refs(request.get("context_refs", []))

        return f"""---
id: {memory_id}
kind: lesson
project: {project}
date: {now.isoformat()}
status: active
tags: []
fingerprint: {self._compute_fingerprint(request)}
context_refs:
{context_refs}
---

# {title}

## Lesson

{request['lesson']}

## Learned From

{request['learned_from']}

## Application

When adding runtime filesystem behavior, test two isolated Brain roots and
verify no cross-contamination.
"""

    def _format_context_refs(self, refs: list[str]) -> str:
        """Format context refs for frontmatter."""
        if not refs:
            return "  []"
        return "\n".join(f"  - {ref}" for ref in refs)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_memory(
        self,
        memory_id: str,
        kind: str,
        project: str,
        path: str,
        sha256: str,
        status: str,
        error: str | None = None,
    ) -> None:
        """Log memory operation."""
        entry = MemoryLogEntry(
            memory_id=memory_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            kind=kind,
            project=project,
            path=path,
            sha256=sha256,
            status=status,
            error=error,
        )

        self.logs_dir.mkdir(parents=True, exist_ok=True)

        log_path = self.logs_dir / "memory.jsonl"

        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "memory_id": entry.memory_id,
                    "timestamp": entry.timestamp,
                    "kind": entry.kind,
                    "project": entry.project,
                    "path": entry.path,
                    "sha256": entry.sha256,
                    "status": entry.status,
                    "error": entry.error,
                }) + "\n")
        except OSError as e:
            logger.error(f"Failed to write memory log: {e}")
            raise

        logger.info(f"Memory {memory_id} {status}: {path}")

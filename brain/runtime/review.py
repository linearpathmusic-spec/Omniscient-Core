"""
Super Brain — Trust & Verification Lifecycle Subsystem

Deep module: Pi sees BrainReviewer.review(). Pi does not see the machinery.

Phase 6: govern the epistemic lifecycle of knowledge with exactly three
states — provisional, verified, disputed — and nothing else.

Core principle:
    Pi interprets; software enforces integrity.

brain_review() changes epistemic state only. It never rewrites article
content or provenance. Verified knowledge is content-locked: the only way
to change it is dispute -> brain_write (auto-resets to provisional) ->
review again.

Architecture:
    - One new public API: brain_review()
    - One deep module: BrainReviewer
    - Two operations: VERIFY, DISPUTE (legal transitions enforced)
    - Stale-review protection via expected_sha256 (STALE_REVIEW)
    - Evidence refs must exist and have intact hashes
    - Only modifies the status field inside knowledge/**/*.md
    - Audit log: logs/reviews.jsonl (rationale, never full source content)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict, TypeAlias

logger = logging.getLogger("brain.review")

from brain.runtime.write import BrainWriter  # noqa: E402  # pyright: ignore[reportMissingImports]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_ROOT = Path(__file__).resolve().parent.parent.parent

# Exactly three epistemic states. No more. No numeric confidence.
ALLOWED_STATUSES = frozenset({"provisional", "verified", "disputed"})

# Legal transitions only. disputed -> verified is deliberately illegal:
# changed content must be re-reviewed after a brain_write (which resets to
# provisional). verified -> anything also goes through dispute first.
LEGAL_TRANSITIONS = {
    "verify": {"provisional": "verified"},
    "dispute": {"provisional": "disputed", "verified": "disputed"},
}

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

ERROR_CODES = frozenset({
    "INVALID_REQUEST",
    "UNSAFE_PATH",
    "TARGET_NOT_FOUND",
    "STALE_REVIEW",
    "INVALID_FRONTMATTER",
    "INVALID_TRANSITION",
    "MISSING_EVIDENCE",
    "INVALID_EVIDENCE",
    "SOURCE_NOT_FOUND",
    "SOURCE_INTEGRITY_FAILED",
    "WRITE_FAILED",
})

# ---------------------------------------------------------------------------
# Discriminated unions — request types
# ---------------------------------------------------------------------------


class VerifyKnowledge(TypedDict):
    """Request to move provisional knowledge to verified."""
    decision: Literal["verify"]
    path: str
    expected_sha256: str
    evidence_refs: list[str]
    rationale: str


class DisputeKnowledge(TypedDict):
    """Request to mark knowledge as disputed (conflicting credible evidence)."""
    decision: Literal["dispute"]
    path: str
    expected_sha256: str
    evidence_refs: list[str]
    rationale: str


ReviewRequest: TypeAlias = VerifyKnowledge | DisputeKnowledge

# ---------------------------------------------------------------------------
# Discriminated unions — result types
# ---------------------------------------------------------------------------


class ReviewVerified(TypedDict):
    """Knowledge successfully verified."""
    status: Literal["verified"]
    review_id: str
    path: str
    previous_status: str
    new_status: str
    content_sha256: str


class ReviewDisputed(TypedDict):
    """Knowledge successfully disputed."""
    status: Literal["disputed"]
    review_id: str
    path: str
    previous_status: str
    new_status: str
    content_sha256: str


class ReviewRejected(TypedDict):
    """Review rejected due to validation failure."""
    status: Literal["rejected"]
    code: str
    message: str


ReviewResult: TypeAlias = ReviewVerified | ReviewDisputed | ReviewRejected

# ---------------------------------------------------------------------------
# Internal data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewLogEntry:
    review_id: str
    timestamp: str
    decision: str
    path: str
    previous_status: str
    new_status: str
    content_sha256: str | None
    evidence_refs: list[str]
    rationale: str
    error: str | None = None


# ---------------------------------------------------------------------------
# BrainReviewer — deep module
# ---------------------------------------------------------------------------


class BrainReviewer:
    """
    Deep module owning all review mechanics behind a simple interface.

    Public API:
        review(request) -> ReviewVerified | ReviewDisputed | ReviewRejected

    Review is NOT owner-gated: Pi decides verify/dispute by reading the
    knowledge and its evidence. The runtime enforces integrity only —
    legal transitions, stale-review protection, source integrity.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or BRAIN_ROOT
        # Reuse BrainWriter for path + source-integrity rules so hashing and
        # knowledge/ confinement live in exactly one place.
        self._writer = BrainWriter(self.root)
        self.knowledge_root = self.root / "knowledge"
        self.logs_dir = self.root / "logs"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def review(self, request: ReviewRequest) -> ReviewResult:
        """Execute review as one locked read/validate/write transaction."""
        with self._writer._mutation_lock():
            return self._review_locked(request)

    def _review_locked(self, request: ReviewRequest) -> ReviewResult:
        """Execute a review. Verifies integrity, then flips status only."""
        decision = request.get("decision")

        if decision not in ("verify", "dispute"):
            return self._reject(
                decision, request.get("path", ""),
                "INVALID_REQUEST",
                f"Unknown decision: {decision}. Must be 'verify' or 'dispute'.",
            )

        path = request.get("path", "")
        expected_sha = request.get("expected_sha256", "")
        evidence_refs = request.get("evidence_refs", [])
        rationale = request.get("rationale", "")

        if not path or not expected_sha or not rationale:
            return self._reject(
                decision, path, "INVALID_REQUEST",
                "Review requires path, expected_sha256, and rationale.",
            )

        # Validate path is a real knowledge/ target. Construct and check
        # containment BEFORE any use: traversal, absolute, and escaping-
        # symlink paths are rejected here.
        try:
            target = Path(self.root) / path
            self._writer._validate_path(target)
        except ValueError as e:
            return self._reject(decision, path, "UNSAFE_PATH", str(e))

        if not target.exists():
            return self._reject(decision, path, "TARGET_NOT_FOUND", f"Target not found: {path}")

        # Stale-review protection: the page must be exactly what Pi read
        current_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        if current_sha != expected_sha:
            return self._reject(
                decision, path, "STALE_REVIEW",
                "The knowledge file changed after it was read. Re-read before reviewing.",
            )

        # Parse current epistemic state
        text = target.read_text(encoding="utf-8")
        fm = self._writer._load_frontmatter(target)
        if not fm or "status" not in fm:
            return self._reject(decision, path, "INVALID_FRONTMATTER",
                                "Target has no valid frontmatter status.")

        current_status = fm["status"]
        if current_status not in ALLOWED_STATUSES:
            return self._reject(decision, path, "INVALID_FRONTMATTER",
                                f"Unknown status: {current_status}")

        # Legal transition check
        allowed = LEGAL_TRANSITIONS[decision]
        if current_status not in allowed:
            return self._reject(
                decision, path, "INVALID_TRANSITION",
                f"'{current_status}' -> '{decision}' is not a legal transition. "
                f"Allowed: {', '.join(f'{k} -> {v}' for k, v in allowed.items())}. "
                "Disputed knowledge must be corrected via brain_write (resets to "
                "provisional) before it can be verified.",
            )
        new_status = allowed[current_status]

        # Evidence integrity: at least one ref, valid format, exists, hash intact
        if not evidence_refs:
            return self._reject(decision, path, "MISSING_EVIDENCE",
                                "Review requires at least one evidence_ref (SRC-*).")

        for ref in evidence_refs:
            if not isinstance(ref, str) or not ref.startswith("SRC-"):
                return self._reject(decision, path, "INVALID_EVIDENCE",
                                    f"Evidence refs must be SRC-* IDs, got: {ref}")
            try:
                self._writer._verify_source_integrity(ref)
            except ValueError as e:
                code = "SOURCE_NOT_FOUND" if "SOURCE_NOT_FOUND" in str(e) else "SOURCE_INTEGRITY_FAILED"
                return self._reject(decision, path, code, str(e))

        # Evidence provenance binding (Phase 6 integrity):
        # Verification evidence must be a non-empty subset of the page's
        # existing source_refs. A page citing source A cannot be verified
        # using unrelated source B — that would break the Constitution's
        # "Conclusion -> Knowledge -> Evidence" chain.
        #
        # Dispute evidence may reasonably be external to existing provenance
        # (the reviewer is introducing new conflicting evidence), but it
        # should be added during the subsequent corrective write.
        if decision == "verify":
            page_source_refs = set(fm.get("source_refs", []))
            evidence_set = set(evidence_refs)
            if not evidence_set.issubset(page_source_refs):
                extra = evidence_set - page_source_refs
                return self._reject(
                    decision, path, "INVALID_EVIDENCE",
                    (
                        f"Verification evidence must be a subset of the page's "
                        f"source_refs. Extra refs not cited by this page: {extra}. "
                        f"Add new evidence via brain_write first, then re-review."
                    ),
                )

        # Surgical status flip: preserve every other byte of the file
        try:
            new_text = self._replace_status_line(text, new_status)
        except ValueError as e:
            return self._reject(decision, path, "INVALID_FRONTMATTER", str(e))

        # Atomic replace (re-verify hash right before the swap)
        try:
            self._atomic_replace(target, new_text, expected_sha)
        except OSError as e:
            return self._reject(decision, path, "WRITE_FAILED", str(e))

        new_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        review_id = self._gen_review_id()
        self._log_review(ReviewLogEntry(
            review_id=review_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            decision=decision,
            path=path,
            previous_status=current_status,
            new_status=new_status,
            content_sha256=new_sha,
            evidence_refs=evidence_refs,
            rationale=rationale,
        ))

        if decision == "verify":
            return ReviewVerified(
                status="verified",
                review_id=review_id,
                path=path,
                previous_status=current_status,
                new_status=new_status,
                content_sha256=new_sha,
            )
        return ReviewDisputed(
            status="disputed",
            review_id=review_id,
            path=path,
            previous_status=current_status,
            new_status=new_status,
            content_sha256=new_sha,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _replace_status_line(self, text: str, new_status: str) -> str:
        """
        Replace only the status field inside YAML frontmatter, preserving
        every other byte of the file exactly.
        """
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError("Target has no YAML frontmatter.")

        fm_lines = parts[1].split("\n")
        out: list[str] = []
        replaced = False
        for line in fm_lines:
            if line.strip().startswith("status:"):
                indent = line[: len(line) - len(line.lstrip())]
                out.append(f"{indent}status: {new_status}")
                replaced = True
            else:
                out.append(line)
        if not replaced:
            raise ValueError("Frontmatter has no status field.")

        return "---".join([parts[0], "\n".join(out), parts[2]])

    def _atomic_replace(self, target: Path, content: str, expected_sha: str) -> None:
        """Replace file atomically; fail if the file changed since the check."""
        current = hashlib.sha256(target.read_bytes()).hexdigest()
        if current != expected_sha:
            raise OSError("STALE_REVIEW: file changed during review.")

        with tempfile.NamedTemporaryFile(
            dir=str(target.parent), suffix=".tmp", delete=False, mode="w"
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            os.replace(tmp_path, str(target))
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _reject(self, decision: str, path: str, code: str, message: str) -> ReviewRejected:
        """Build a rejection result and log it (no source content in logs)."""
        self._log_review(ReviewLogEntry(
            review_id=self._gen_review_id(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            decision=decision,
            path=path,
            previous_status="",
            new_status="",
            content_sha256=None,
            evidence_refs=[],
            rationale="",
            error=f"{code}: {message}",
        ))
        return ReviewRejected(status="rejected", code=code, message=message)

    def _log_review(self, entry: ReviewLogEntry) -> None:
        """Append a review audit entry to logs/reviews.jsonl."""
        try:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            with open(self.logs_dir / "reviews.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "review_id": entry.review_id,
                    "timestamp": entry.timestamp,
                    "decision": entry.decision,
                    "path": entry.path,
                    "previous_status": entry.previous_status or None,
                    "new_status": entry.new_status or None,
                    "content_sha256": entry.content_sha256,
                    "evidence_refs": entry.evidence_refs,
                    "rationale": entry.rationale,
                    "error": entry.error,
                }) + "\n")
        except OSError as e:
            logger.warning("Failed to write review log: %s", e)

    def _gen_review_id(self) -> str:
        """Generate a short review ID: BRV-<6 hex>."""
        import secrets
        return f"BRV-{secrets.token_hex(3).upper()}"

"""
Super Brain — Controlled Procedural Learning Subsystem

Deep module: Pi sees BrainSkills.propose(), BrainSkills.approve(), BrainSkills.reject().
Pi does not see the machinery.

Phase 5: turn selected lessons into procedural memory (skills) without
allowing silent self-modification.

Core principle:
    Lessons may suggest procedures. They do not automatically become procedures.

Architecture:
    - One new public API: brain_skill()
    - One deep module: BrainSkills
    - Four operations: PROPOSE (create/update), APPROVE, REJECT
    - Pi proposes only, owner approves/rejects (mechanical auth)
    - Skills = Markdown only, no executable code
    - Only modifies skills/**/SKILL.md
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
from pathlib import Path
from typing import Literal, TypedDict, TypeAlias

logger = logging.getLogger("brain.skills")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_ROOT = Path(__file__).resolve().parent.parent.parent

SKILLS_ROOT = BRAIN_ROOT / "skills"

PROPOSALS_PENDING = BRAIN_ROOT / "proposals" / "skills" / "pending"

PROPOSALS_RESOLVED = BRAIN_ROOT / "proposals" / "skills" / "resolved"

LOGS_DIR = BRAIN_ROOT / "logs"

SKILLS_LOG = LOGS_DIR / "skills.jsonl"

# Allowed skill scopes (metadata only, not a routing subsystem)
ALLOWED_SCOPES = frozenset({
    "research",
    "coding",
    "debugging",
    "planning",
    "security",
    "operations",
})

# Allowed skill statuses (Phase 5: only active)
ALLOWED_STATUSES = frozenset({"active"})

# Reserved metadata fields that cannot appear in skill content
RESERVED_FIELDS = frozenset({
    "authority",
    "override_agents",
    "override_constitution",
    "autonomy_level",
})

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

ERROR_CODES = frozenset({
    "INVALID_REQUEST",
    "INVALID_OPERATION",
    "INVALID_SKILL_SCHEMA",
    "INVALID_TARGET_PATH",
    "TARGET_NOT_FOUND",
    "TARGET_EXISTS",
    "DUPLICATE_SKILL",
    "STALE_PROPOSAL",
    "PROPOSAL_NOT_PENDING",
    "VERSION_MISMATCH",
    "MISSING_EVIDENCE",
    "UNAUTHORIZED_OPERATION",
    "WRITE_FAILED",
    "UNSAFE_PATH",
    "RESERVED_FIELD",
})

# ---------------------------------------------------------------------------
# Discriminated unions — request types
# ---------------------------------------------------------------------------


class ProposeSkillCreate(TypedDict):
    """Request to propose a new skill."""
    op: Literal["propose_create"]
    skill_name: str
    purpose: str
    proposed_content: str
    evidence_refs: list[str]


class ProposeSkillUpdate(TypedDict):
    """Request to propose an update to an existing skill."""
    op: Literal["propose_update"]
    skill_path: str
    expected_sha256: str
    rationale: str
    proposed_content: str
    evidence_refs: list[str]


class ApproveSkillProposal(TypedDict):
    """Request to approve a pending proposal (owner only)."""
    op: Literal["approve"]
    proposal_id: str
    expected_proposal_sha256: str


class RejectSkillProposal(TypedDict):
    """Request to reject a pending proposal (owner only)."""
    op: Literal["reject"]
    proposal_id: str
    reason: str


SkillRequest = (
    ProposeSkillCreate
    | ProposeSkillUpdate
    | ApproveSkillProposal
    | RejectSkillProposal
)

# ---------------------------------------------------------------------------
# Discriminated unions — result types
# ---------------------------------------------------------------------------


class ProposalCreated(TypedDict):
    """Proposal successfully created."""
    status: Literal["created"]
    proposal_id: str
    path: str
    sha256: str


class ProposalApplied(TypedDict):
    """Proposal successfully applied (skill mutated)."""
    status: Literal["applied"]
    proposal_id: str
    skill_path: str
    old_version: int
    new_version: int
    sha256: str


class ProposalRejected(TypedDict):
    """Proposal successfully rejected."""
    status: Literal["rejected"]
    proposal_id: str
    reason: str


class OperationRejected(TypedDict):
    """Operation rejected due to validation failure."""
    status: Literal["rejected"]
    code: str
    message: str


SkillResult = (
    ProposalCreated
    | ProposalApplied
    | ProposalRejected
    | OperationRejected
)

# ---------------------------------------------------------------------------
# Internal data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillLogEntry:
    """Log entry for skill operations."""
    proposal_id: str
    timestamp: str
    operation: str
    target: str
    status: str
    old_version: int | None = None
    new_version: int | None = None
    evidence_refs: list[str] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# BrainSkills — deep module
# ---------------------------------------------------------------------------


class BrainSkills:
    """
    Deep module owning all procedural-learning mechanics behind a simple interface.

    Public API:
        propose(request) -> ProposalCreated | OperationRejected
        approve(request) -> ProposalApplied | OperationRejected
        reject(request) -> ProposalRejected | OperationRejected
    """

    def __init__(self, root: Path | None = None, is_owner: bool = False) -> None:
        self.root = root or BRAIN_ROOT
        self.skills_root = self.root / "skills"
        self.proposals_pending = self.root / "proposals" / "skills" / "pending"
        self.proposals_resolved = self.root / "proposals" / "skills" / "resolved"
        self.logs_dir = self.root / "logs"
        self.is_owner = is_owner

        # Ensure directories exist
        self.skills_root.mkdir(parents=True, exist_ok=True)
        self.proposals_pending.mkdir(parents=True, exist_ok=True)
        self.proposals_resolved.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def propose(self, request: ProposeSkillCreate | ProposeSkillUpdate) -> SkillResult:
        """
        Propose a new skill or an update to an existing skill.

        Args:
            request: Either ProposeSkillCreate or ProposeSkillUpdate.

        Returns:
            ProposalCreated or OperationRejected.
        """
        op = request.get("op")

        if op == "propose_create":
            return self._propose_create(request)  # type: ignore[arg-type]
        elif op == "propose_update":
            return self._propose_update(request)  # type: ignore[arg-type]
        else:
            return OperationRejected(
                status="rejected",
                code="INVALID_OPERATION",
                message=f"Unknown operation: {op}. Must be 'propose_create' or 'propose_update'.",
            )

    def approve(self, request: ApproveSkillProposal) -> SkillResult:
        """
        Approve a pending proposal (owner only).

        Args:
            request: ApproveSkillProposal with proposal_id and expected_sha256.

        Returns:
            ProposalApplied or OperationRejected.
        """
        if not self.is_owner:
            return OperationRejected(
                status="rejected",
                code="UNAUTHORIZED_OPERATION",
                message="Approval requires owner authorization. Use --owner flag.",
            )

        proposal_id = request.get("proposal_id", "")
        expected_sha = request.get("expected_proposal_sha256", "")

        if not proposal_id or not expected_sha:
            return OperationRejected(
                status="rejected",
                code="INVALID_REQUEST",
                message="Approval requires proposal_id and expected_proposal_sha256.",
            )

        # Find pending proposal
        proposal_path = self.proposals_pending / f"{proposal_id}.md"
        if not proposal_path.exists():
            return OperationRejected(
                status="rejected",
                code="PROPOSAL_NOT_PENDING",
                message=f"Proposal {proposal_id} not found in pending.",
            )

        # Verify proposal hash
        actual_sha = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            return OperationRejected(
                status="rejected",
                code="STALE_PROPOSAL",
                message=f"Proposal {proposal_id} content changed. Expected {expected_sha[:8]}..., got {actual_sha[:8]}...",
            )

        # Parse proposal to get operation, target, and proposed content
        proposal_content = proposal_path.read_text()
        operation, target_path, proposed_content, expected_skill_sha = self._parse_proposal(proposal_content)

        # Validate target path
        try:
            self._validate_target_path(target_path)
        except ValueError as e:
            return OperationRejected(
                status="rejected",
                code="INVALID_TARGET_PATH",
                message=str(e),
            )

        # Validate proposed skill schema
        try:
            self._validate_skill_schema(proposed_content)
        except ValueError as e:
            return OperationRejected(
                status="rejected",
                code="INVALID_SKILL_SCHEMA",
                message=str(e),
            )

        # Check the live skill state: stale detection + current version
        target_full_path = self.skills_root / target_path
        if operation == "update":
            if not target_full_path.exists():
                return OperationRejected(
                    status="rejected",
                    code="TARGET_NOT_FOUND",
                    message=f"Skill no longer exists: {target_path}",
                )
            current_sha = hashlib.sha256(target_full_path.read_bytes()).hexdigest()
            if expected_skill_sha and current_sha != expected_skill_sha:
                return OperationRejected(
                    status="rejected",
                    code="STALE_PROPOSAL",
                    message="Skill content changed since proposal. Create a new proposal.",
                )
            current_version = self._frontmatter_version(target_full_path.read_text())
        else:
            if target_full_path.exists():
                return OperationRejected(
                    status="rejected",
                    code="STALE_PROPOSAL",
                    message="Skill already exists. Propose an update instead.",
                )
            current_version = 0

        # Version integrity: create starts at 1, update increments exactly once
        proposed_version = self._frontmatter_version(proposed_content)
        expected_version = current_version + 1
        if proposed_version != expected_version:
            return OperationRejected(
                status="rejected",
                code="VERSION_MISMATCH",
                message=(
                    f"Version mismatch: current {current_version}, "
                    f"proposed {proposed_version}, expected {expected_version}."
                ),
            )
        new_version = proposed_version

        # Atomic write
        tmp_path = ""
        try:
            target_full_path.parent.mkdir(parents=True, exist_ok=True)

            with tempfile.NamedTemporaryFile(
                dir=str(target_full_path.parent), suffix=".tmp", delete=False, mode="w"
            ) as tmp:
                tmp.write(proposed_content)
                tmp_path = tmp.name
            os.replace(tmp_path, str(target_full_path))
        except OSError as e:
            logger.error(f"Write failed: {e}")
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError as cleanup_err:
                    logger.warning(f"Cleanup failed: {cleanup_err}")
            return OperationRejected(
                status="rejected",
                code="WRITE_FAILED",
                message=str(e),
            )

        # Move proposal to resolved
        resolved_path = self.proposals_resolved / f"{proposal_id}.md"
        proposal_path.rename(resolved_path)

        # Update status in resolved proposal
        self._update_proposal_status(resolved_path, "applied")

        # Compute SHA of new skill
        new_sha = hashlib.sha256(proposed_content.encode("utf-8")).hexdigest()

        # Log
        self._log_skill(
            proposal_id=proposal_id,
            operation="approve",
            target=str(target_path),
            status="applied",
            old_version=current_version,
            new_version=new_version,
            sha256=new_sha,
        )

        return ProposalApplied(
            status="applied",
            proposal_id=proposal_id,
            skill_path=str(target_path),
            old_version=current_version,
            new_version=new_version,
            sha256=new_sha,
        )

    def reject(self, request: RejectSkillProposal) -> SkillResult:
        """
        Reject a pending proposal (owner only).

        Args:
            request: RejectSkillProposal with proposal_id and reason.

        Returns:
            ProposalRejected or OperationRejected.
        """
        if not self.is_owner:
            return OperationRejected(
                status="rejected",
                code="UNAUTHORIZED_OPERATION",
                message="Rejection requires owner authorization. Use --owner flag.",
            )

        proposal_id = request.get("proposal_id", "")
        reason = request.get("reason", "")

        if not proposal_id or not reason:
            return OperationRejected(
                status="rejected",
                code="INVALID_REQUEST",
                message="Rejection requires proposal_id and reason.",
            )

        # Find pending proposal
        proposal_path = self.proposals_pending / f"{proposal_id}.md"
        if not proposal_path.exists():
            return OperationRejected(
                status="rejected",
                code="PROPOSAL_NOT_PENDING",
                message=f"Proposal {proposal_id} not found in pending.",
            )

        # Move proposal to resolved
        resolved_path = self.proposals_resolved / f"{proposal_id}.md"
        proposal_path.rename(resolved_path)

        # Update status in resolved proposal
        self._update_proposal_status(resolved_path, "rejected", reason)

        # Log
        self._log_skill(
            proposal_id=proposal_id,
            operation="reject",
            target="",
            status="rejected",
            reason=reason,
        )

        return ProposalRejected(
            status="rejected",
            proposal_id=proposal_id,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Propose CREATE
    # ------------------------------------------------------------------

    def _propose_create(self, request: ProposeSkillCreate) -> SkillResult:
        """Validate and create a new skill proposal."""
        skill_name = request.get("skill_name", "")
        purpose = request.get("purpose", "")
        proposed_content = request.get("proposed_content", "")
        evidence_refs = request.get("evidence_refs", [])

        # Validate required fields
        if not skill_name or not purpose or not proposed_content:
            return OperationRejected(
                status="rejected",
                code="INVALID_REQUEST",
                message="Create proposal requires skill_name, purpose, and proposed_content.",
            )

        # Validate evidence_refs (Milestone 0: both procedural + mechanical)
        if not evidence_refs:
            return OperationRejected(
                status="rejected",
                code="MISSING_EVIDENCE",
                message="Proposal requires at least one evidence_ref (LES-, DEC-, SRC-, or eval case ID).",
            )

        if not self._validate_evidence_refs(evidence_refs):
            return OperationRejected(
                status="rejected",
                code="MISSING_EVIDENCE",
                message="evidence_refs must reference LES-, DEC-, SRC- IDs or eval case IDs.",
            )

        # Validate derived target path (blocks traversal / escapes from skills/)
        try:
            self._validate_target_path(f"{skill_name}/SKILL.md")
        except ValueError as e:
            return OperationRejected(
                status="rejected",
                code="INVALID_TARGET_PATH",
                message=str(e),
            )

        # Check for duplicate skill name (existing skill or pending create proposal)
        if self._skill_exists(skill_name) or self._pending_create_exists(skill_name):
            return OperationRejected(
                status="rejected",
                code="DUPLICATE_SKILL",
                message=(
                    f"Skill '{skill_name}' already exists or has a pending create "
                    "proposal. Propose an update instead."
                ),
            )

        # Generate proposal ID
        proposal_id = self._generate_proposal_id()

        # Build proposal content
        content = self._build_proposal_content(
            proposal_id=proposal_id,
            operation="create",
            skill_name=skill_name,
            purpose=purpose,
            proposed_content=proposed_content,
            evidence_refs=evidence_refs,
        )

        # Atomic write
        tmp_path = ""
        try:
            target_dir = self.proposals_pending
            target_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=str(target_dir), suffix=".tmp", delete=False, mode="w"
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            os.replace(tmp_path, str(target_dir / f"{proposal_id}.md"))
        except OSError as e:
            logger.error(f"Write failed: {e}")
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError as cleanup_err:
                    logger.warning(f"Cleanup failed: {cleanup_err}")
            return OperationRejected(
                status="rejected",
                code="WRITE_FAILED",
                message=str(e),
            )

        # Compute SHA
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Log
        self._log_skill(
            proposal_id=proposal_id,
            operation="propose_create",
            target=f"skills/{skill_name}/SKILL.md",
            status="pending",
            evidence_refs=evidence_refs,
        )

        return ProposalCreated(
            status="created",
            proposal_id=proposal_id,
            path=str(target_dir / f"{proposal_id}.md"),
            sha256=sha,
        )

    # ------------------------------------------------------------------
    # Propose UPDATE
    # ------------------------------------------------------------------

    def _propose_update(self, request: ProposeSkillUpdate) -> SkillResult:
        """Validate and create an update proposal."""
        skill_path = request.get("skill_path", "")
        expected_sha = request.get("expected_sha256", "")
        rationale = request.get("rationale", "")
        proposed_content = request.get("proposed_content", "")
        evidence_refs = request.get("evidence_refs", [])

        # Validate required fields
        if not skill_path or not expected_sha or not rationale or not proposed_content:
            return OperationRejected(
                status="rejected",
                code="INVALID_REQUEST",
                message="Update proposal requires skill_path, expected_sha256, rationale, and proposed_content.",
            )

        # Validate evidence_refs
        if not evidence_refs:
            return OperationRejected(
                status="rejected",
                code="MISSING_EVIDENCE",
                message="Proposal requires at least one evidence_ref.",
            )

        if not self._validate_evidence_refs(evidence_refs):
            return OperationRejected(
                status="rejected",
                code="MISSING_EVIDENCE",
                message="evidence_refs must reference LES-, DEC-, SRC- IDs or eval case IDs.",
            )

        # Validate target path
        try:
            self._validate_target_path(skill_path)
        except ValueError as e:
            return OperationRejected(
                status="rejected",
                code="INVALID_TARGET_PATH",
                message=str(e),
            )

        # Verify target exists and hash matches
        target_full_path = self.skills_root / skill_path
        if not target_full_path.exists():
            return OperationRejected(
                status="rejected",
                code="TARGET_NOT_FOUND",
                message=f"Skill not found: {skill_path}",
            )

        actual_sha = hashlib.sha256(target_full_path.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            return OperationRejected(
                status="rejected",
                code="STALE_PROPOSAL",
                message=f"Skill content changed. Expected {expected_sha[:8]}..., got {actual_sha[:8]}...",
            )

        # Generate proposal ID
        proposal_id = self._generate_proposal_id()

        # Build proposal content
        content = self._build_proposal_content(
            proposal_id=proposal_id,
            operation="update",
            skill_path=skill_path,
            expected_sha=expected_sha,
            rationale=rationale,
            proposed_content=proposed_content,
            evidence_refs=evidence_refs,
        )

        # Atomic write
        tmp_path = ""
        try:
            target_dir = self.proposals_pending
            target_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=str(target_dir), suffix=".tmp", delete=False, mode="w"
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            os.replace(tmp_path, str(target_dir / f"{proposal_id}.md"))
        except OSError as e:
            logger.error(f"Write failed: {e}")
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError as cleanup_err:
                    logger.warning(f"Cleanup failed: {cleanup_err}")
            return OperationRejected(
                status="rejected",
                code="WRITE_FAILED",
                message=str(e),
            )

        # Compute SHA
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Log
        self._log_skill(
            proposal_id=proposal_id,
            operation="propose_update",
            target=str(skill_path),
            status="pending",
            evidence_refs=evidence_refs,
        )

        return ProposalCreated(
            status="created",
            proposal_id=proposal_id,
            path=str(target_dir / f"{proposal_id}.md"),
            sha256=sha,
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_target_path(self, path: str) -> None:
        """Ensure path is under skills/ and has SKILL.md suffix."""
        if not path.endswith("/SKILL.md"):
            raise ValueError(f"Target must end with /SKILL.md: {path}")

        # Check for path traversal
        if ".." in path:
            raise ValueError(f"Path traversal not allowed: {path}")

        # Resolve and verify under skills_root
        try:
            full_path = (self.skills_root / path).resolve()
            full_path.relative_to(self.skills_root.resolve())
        except ValueError:
            raise ValueError(f"Target escapes skills/: {path}")

    def _validate_skill_schema(self, content: str) -> None:
        """Validate proposed skill content has required frontmatter fields."""
        if not content.startswith("---"):
            raise ValueError("Skill content must start with YAML frontmatter (---).")

        parts = content.split("---", 2)
        if len(parts) < 3:
            raise ValueError("Invalid frontmatter: must have opening and closing ---.")

        fm_text = parts[1].strip()
        body = parts[2].strip()

        # Parse minimal YAML (key: value pairs)
        fm: dict[str, str] = {}
        for line in fm_text.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if ":" in stripped:
                key, _, value = stripped.partition(":")
                fm[key.strip()] = value.strip()

        # Check required fields
        required = {"name", "scope", "status", "version"}
        missing = required - set(fm.keys())
        if missing:
            raise ValueError(f"Skill missing required fields: {missing}")

        # Check scope is allowed
        scope = fm.get("scope", "")
        if scope not in ALLOWED_SCOPES:
            raise ValueError(f"Unknown scope: {scope}. Allowed: {ALLOWED_SCOPES}")

        # Check status is allowed
        status = fm.get("status", "")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"Unknown status: {status}. Allowed: {ALLOWED_STATUSES}")

        # Check version is integer
        version_str = fm.get("version", "")
        try:
            version = int(version_str)
            if version < 1:
                raise ValueError("Version must be >= 1")
        except ValueError as e:
            if "Version must be" in str(e):
                raise
            raise ValueError(f"Version must be an integer: {version_str}")

        # Check for reserved fields
        reserved_found = set(fm.keys()) & RESERVED_FIELDS
        if reserved_found:
            raise ValueError(f"Reserved fields not allowed: {reserved_found}")

        # Check body is not empty
        if not body:
            raise ValueError("Skill body must not be empty.")

    def _validate_evidence_refs(self, refs: list[str]) -> bool:
        """Validate evidence_refs reference valid ID patterns."""
        valid_patterns = [
            r"^LES-[A-Za-z0-9-]+$",      # Lesson IDs
            r"^DEC-[A-Za-z0-9-]+$",      # Decision IDs
            r"^SRC-[A-Za-z0-9-]+$",      # Source IDs
            r"^eval-[a-z0-9-]+$",        # Eval case IDs
        ]

        for ref in refs:
            if not any(re.match(pattern, ref) for pattern in valid_patterns):
                return False
        return True

    def _pending_create_exists(self, skill_name: str) -> bool:
        """Check for a pending create proposal targeting the same skill name."""
        target = f"{skill_name}/SKILL.md"
        for proposal_file in self.proposals_pending.glob("*.md"):
            try:
                op, prop_target, _, _ = self._parse_proposal(proposal_file.read_text())
            except ValueError:
                continue
            if op == "create" and prop_target == target:
                return True
        return False

    def _skill_exists(self, skill_name: str) -> bool:
        """Check if a skill with the given name already exists."""
        skill_path = self.skills_root / skill_name / "SKILL.md"
        return skill_path.exists()

    # ------------------------------------------------------------------
    # Proposal helpers
    # ------------------------------------------------------------------

    def _generate_proposal_id(self) -> str:
        """Generate a unique proposal ID: SKP-<short random hash>."""
        import secrets
        random_suffix = secrets.token_hex(3).upper()
        return f"SKP-{random_suffix}"

    def _build_proposal_content(
        self,
        proposal_id: str,
        operation: str,
        skill_name: str | None = None,
        skill_path: str | None = None,
        expected_sha: str | None = None,
        purpose: str | None = None,
        rationale: str | None = None,
        proposed_content: str | None = None,
        evidence_refs: list[str] | None = None,
    ) -> str:
        """Build markdown content for a proposal file."""
        evidence_refs_yaml = "\n".join(f"  - {ref}" for ref in (evidence_refs or []))

        if operation == "create":
            return f"""---
proposal_id: {proposal_id}
operation: create
target: {skill_name}/SKILL.md
created_at: {datetime.now(timezone.utc).isoformat()}
status: pending
evidence_refs:
{evidence_refs_yaml}
---

# Proposal: {skill_name}

## Purpose

{purpose}

## Proposed Skill

{proposed_content}
"""
        else:  # update
            return f"""---
proposal_id: {proposal_id}
operation: update
target: {skill_path}
expected_sha256: {expected_sha}
created_at: {datetime.now(timezone.utc).isoformat()}
status: pending
evidence_refs:
{evidence_refs_yaml}
---

# Proposal: Update {skill_path}

## Rationale

{rationale}

## Proposed Replacement

{proposed_content}
"""

    def _parse_proposal(self, content: str) -> tuple[str, str, str, str | None]:
        """Return (operation, target, proposed_content, expected_sha256)."""
        if not content.startswith("---"):
            raise ValueError("Invalid proposal: missing frontmatter.")

        parts = content.split("---", 2)
        if len(parts) < 3:
            raise ValueError("Invalid proposal: malformed frontmatter.")

        fm_text = parts[1].strip()
        body = parts[2].strip()

        operation = ""
        target = ""
        expected_sha: str | None = None
        for line in fm_text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("operation:"):
                operation = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("target:"):
                target = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("expected_sha256:"):
                expected_sha = stripped.split(":", 1)[1].strip()

        proposed_content = ""
        if "## Proposed Skill" in body:
            proposed_content = body.split("## Proposed Skill", 1)[1].strip()
        elif "## Proposed Replacement" in body:
            proposed_content = body.split("## Proposed Replacement", 1)[1].strip()

        return operation, target, proposed_content, expected_sha

    def _parse_frontmatter(self, content: str) -> dict[str, str]:
        """Minimal YAML frontmatter parser (key: value pairs)."""
        if not content.startswith("---"):
            return {}
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        fm: dict[str, str] = {}
        for line in parts[1].split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or ":" not in stripped:
                continue
            key, _, value = stripped.partition(":")
            fm[key.strip()] = value.strip()
        return fm

    def _frontmatter_version(self, content: str) -> int:
        """Parse the version field from frontmatter as an int (0 if absent/invalid)."""
        try:
            return int(self._parse_frontmatter(content).get("version", 0))
        except (TypeError, ValueError):
            return 0

    def _update_proposal_status(self, path: Path, status: str, reason: str | None = None) -> None:
        """Update the status field in a resolved proposal file."""
        content = path.read_text()
        lines = content.split("\n")
        new_lines = []
        for line in lines:
            if line.strip().startswith("status:"):
                new_lines.append(f"status: {status}")
                if reason:
                    new_lines.append(f"rejected_reason: {reason}")
            else:
                new_lines.append(line)
        path.write_text("\n".join(new_lines))

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_skill(
        self,
        proposal_id: str,
        operation: str,
        target: str,
        status: str,
        old_version: int | None = None,
        new_version: int | None = None,
        evidence_refs: list[str] | None = None,
        sha256: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Append a skill operation log entry."""
        entry = SkillLogEntry(
            proposal_id=proposal_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            operation=operation,
            target=target,
            status=status,
            old_version=old_version,
            new_version=new_version,
            evidence_refs=evidence_refs,
            error=reason,
        )

        log_path = self.logs_dir / "skills.jsonl"

        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "proposal_id": entry.proposal_id,
                    "timestamp": entry.timestamp,
                    "operation": entry.operation,
                    "target": entry.target,
                    "status": entry.status,
                    "old_version": entry.old_version,
                    "new_version": entry.new_version,
                    "evidence_refs": entry.evidence_refs,
                    "sha256": sha256,
                    "error": entry.error,
                }) + "\n")
        except OSError as e:
            logger.error(f"Failed to write skills log: {e}")

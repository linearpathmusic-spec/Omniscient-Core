"""
Super Brain — Phase 5 Skill Management Tests

Tests for BrainSkills deep module:
- State contracts (Milestone 1)
- Proposal creation (Milestone 2)
- Authorization boundary (Milestone 3)
- Controlled approval (Milestone 4)
- Security boundaries
- Concurrency (stale proposals)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import cast

import pytest

from brain.runtime.skills import (
    BrainSkills,
    ERROR_CODES,
    ProposeSkillCreate,
    ProposeSkillUpdate,
    RejectSkillProposal,
    SkillResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_root():
    """Create a temporary brain root for tests."""
    root = Path(tempfile.mkdtemp())
    yield root
    shutil.rmtree(root)


@pytest.fixture
def brain(temp_root):
    """Create a BrainSkills instance with temp root."""
    return BrainSkills(root=temp_root, is_owner=False)


@pytest.fixture
def owner_brain(temp_root):
    """Create a BrainSkills instance with owner authorization."""
    return BrainSkills(root=temp_root, is_owner=True)


@pytest.fixture
def sample_skill_content():
    """Sample SKILL.md content for testing."""
    return """---
name: Test Skill
scope: debugging
status: active
version: 1
---

# Test Skill

## Procedure

1. Do something.
2. Verify the result.
"""


@pytest.fixture
def sample_skill_path():
    """Sample skill path for testing."""
    return "debugging/SKILL.md"


# ---------------------------------------------------------------------------
# Milestone 1 — State contracts
# ---------------------------------------------------------------------------


class TestStateContracts:
    """Test that invalid state combinations are hard to represent."""

    def test_propose_create_requires_fields(self, brain):
        """Create proposal requires skill_name, purpose, proposed_content, evidence_refs."""
        request = {
            "op": "propose_create",
            # Missing skill_name, purpose, proposed_content, evidence_refs
        }
        result = brain.propose(request)
        assert result["status"] == "rejected"
        assert result["code"] == "INVALID_REQUEST"

    def test_propose_update_requires_fields(self, brain):
        """Update proposal requires skill_path, expected_sha256, rationale, proposed_content, evidence_refs."""
        request = {
            "op": "propose_update",
            # Missing all required fields
        }
        result = brain.propose(request)
        assert result["status"] == "rejected"
        assert result["code"] == "INVALID_REQUEST"

    def test_approve_requires_fields(self, owner_brain):
        """Approve requires proposal_id and expected_proposal_sha256."""
        request = {
            "op": "approve",
            # Missing proposal_id and expected_proposal_sha256
        }
        result = owner_brain.approve(request)
        assert result["status"] == "rejected"
        assert result["code"] == "INVALID_REQUEST"

    def test_reject_requires_fields(self, owner_brain):
        """Reject requires proposal_id and reason."""
        request = {
            "op": "reject",
            # Missing proposal_id and reason
        }
        result = owner_brain.reject(request)
        assert result["status"] == "rejected"
        assert result["code"] == "INVALID_REQUEST"

    def test_invalid_operation(self, brain):
        """Unknown operation is rejected."""
        request = {"op": "unknown_operation"}
        result = brain.propose(request)
        assert result["status"] == "rejected"
        assert result["code"] == "INVALID_OPERATION"


# ---------------------------------------------------------------------------
# Milestone 2 — Proposal creation
# ---------------------------------------------------------------------------


class TestProposalCreation:
    """Test proposal creation with validation, ID generation, duplicate protection."""

    def test_create_proposal_success(self, brain, sample_skill_content):
        """Valid create proposal is accepted."""
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "debugging",
            "purpose": "Test purpose",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["LES-test-001"],
        }
        result = brain.propose(request)
        assert result["status"] == "created"
        assert result["proposal_id"].startswith("SKP-")
        assert result["path"].endswith(f"{result['proposal_id']}.md")

    def test_create_proposal_duplicate(self, brain, sample_skill_content):
        """Duplicate skill name is rejected."""
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "debugging",
            "purpose": "Test purpose",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["LES-test-001"],
        }
        brain.propose(request)  # First proposal succeeds

        # Second proposal with same name should fail
        result = brain.propose(request)
        assert result["status"] == "rejected"
        assert result["code"] == "DUPLICATE_SKILL"

    def test_create_proposal_missing_evidence(self, brain, sample_skill_content):
        """Proposal without evidence_refs is rejected."""
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "debugging",
            "purpose": "Test purpose",
            "proposed_content": sample_skill_content,
            "evidence_refs": [],
        }
        result = brain.propose(request)
        assert result["status"] == "rejected"
        assert result["code"] == "MISSING_EVIDENCE"

    def test_create_proposal_invalid_evidence_format(self, brain, sample_skill_content):
        """Proposal with invalid evidence_ref format is rejected."""
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "debugging",
            "purpose": "Test purpose",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["invalid-ref"],
        }
        result = brain.propose(request)
        assert result["status"] == "rejected"
        assert result["code"] == "MISSING_EVIDENCE"

    def test_update_proposal_target_not_found(self, brain):
        """Update proposal for non-existent skill is rejected."""
        request: ProposeSkillUpdate = {
            "op": "propose_update",
            "skill_path": "nonexistent/SKILL.md",
            "expected_sha256": "abc123",
            "rationale": "Test rationale",
            "proposed_content": "content",
            "evidence_refs": ["LES-test-001"],
        }
        result = brain.propose(request)
        assert result["status"] == "rejected"
        assert result["code"] == "TARGET_NOT_FOUND"

    def test_update_proposal_hash_mismatch(self, brain, sample_skill_path, sample_skill_content):
        """Update proposal with wrong expected_sha256 is rejected."""
        # Create the skill first
        skill_dir = brain.skills_root / sample_skill_path.rsplit("/", 1)[0]
        skill_dir.mkdir(parents=True, exist_ok=True)
        (brain.skills_root / sample_skill_path).write_text(sample_skill_content)

        actual_sha = hashlib.sha256(sample_skill_content.encode()).hexdigest()

        request: ProposeSkillUpdate = {
            "op": "propose_update",
            "skill_path": sample_skill_path,
            "expected_sha256": "wrong_hash",  # Wrong hash
            "rationale": "Test rationale",
            "proposed_content": "new content",
            "evidence_refs": ["LES-test-001"],
        }
        result = brain.propose(request)
        assert result["status"] == "rejected"
        assert result["code"] == "STALE_PROPOSAL"

    def test_proposal_id_format(self, brain, sample_skill_content):
        """Proposal IDs follow SKP-<hex> format."""
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "test-skill",
            "purpose": "Test",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["LES-test-001"],
        }
        result = brain.propose(request)
        assert result["status"] == "created"
        proposal_id = result["proposal_id"]
        assert proposal_id.startswith("SKP-")
        assert len(proposal_id) == 10  # SKP- + 6 hex chars


# ---------------------------------------------------------------------------
# Milestone 3 — Authorization boundary
# ---------------------------------------------------------------------------


class TestAuthorizationBoundary:
    """Test that Pi cannot approve its own proposals."""

    def test_agent_cannot_approve(self, brain, sample_skill_content):
        """Agent (non-owner) cannot approve proposals."""
        # First create a proposal
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "test",
            "purpose": "Test",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["LES-test-001"],
        }
        create_result = brain.propose(request)
        assert create_result["status"] == "created"

        # Try to approve as agent
        proposal_path = brain.proposals_pending / f"{create_result['proposal_id']}.md"
        actual_sha = hashlib.sha256(proposal_path.read_bytes()).hexdigest()

        approve_request = {
            "op": "approve",
            "proposal_id": create_result["proposal_id"],
            "expected_proposal_sha256": actual_sha,
        }
        result = brain.approve(approve_request)
        assert result["status"] == "rejected"
        assert result["code"] == "UNAUTHORIZED_OPERATION"

    def test_agent_cannot_reject(self, brain, sample_skill_content):
        """Agent (non-owner) cannot reject proposals."""
        # First create a proposal
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "test",
            "purpose": "Test",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["LES-test-001"],
        }
        create_result = brain.propose(request)

        # Try to reject as agent
        reject_request = {
            "op": "reject",
            "proposal_id": create_result["proposal_id"],
            "reason": "Test rejection",
        }
        result = brain.reject(reject_request)
        assert result["status"] == "rejected"
        assert result["code"] == "UNAUTHORIZED_OPERATION"

    def test_owner_can_approve(self, owner_brain, sample_skill_content):
        """Owner can approve proposals."""
        # Create proposal as agent
        agent_brain = BrainSkills(root=owner_brain.root, is_owner=False)
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "test",
            "purpose": "Test",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["LES-test-001"],
        }
        create_result = agent_brain.propose(request)
        assert create_result["status"] == "created", f"Expected created, got {create_result}"

        # Approve as owner
        proposal_path = owner_brain.proposals_pending / f"{create_result['proposal_id']}.md"
        actual_sha = hashlib.sha256(proposal_path.read_bytes()).hexdigest()

        approve_request = {
            "op": "approve",
            "proposal_id": create_result["proposal_id"],
            "expected_proposal_sha256": actual_sha,
        }
        result = owner_brain.approve(approve_request)
        assert result["status"] == "applied"

    def test_owner_can_reject(self, owner_brain, sample_skill_content):
        """Owner can reject proposals."""
        # Create proposal as agent
        agent_brain = BrainSkills(root=owner_brain.root, is_owner=False)
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "test",
            "purpose": "Test",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["LES-test-001"],
        }
        create_result = agent_brain.propose(request)
        assert create_result["status"] == "created", f"Expected created, got {create_result}"

        # Reject as owner
        reject_request = {
            "op": "reject",
            "proposal_id": create_result["proposal_id"],
            "reason": "Not ready yet",
        }
        result = owner_brain.reject(reject_request)
        assert result["status"] == "rejected"
        assert result["reason"] == "Not ready yet"


# ---------------------------------------------------------------------------
# Milestone 4 — Controlled approval
# ---------------------------------------------------------------------------


class TestControlledApproval:
    """Test approval with optimistic concurrency, schema validation, versioning."""

    def test_approve_moves_proposal_to_resolved(self, owner_brain, sample_skill_content):
        """Approved proposal moves from pending to resolved."""
        # Create proposal
        agent_brain = BrainSkills(root=owner_brain.root, is_owner=False)
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "test",
            "purpose": "Test",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["LES-test-001"],
        }
        create_result = agent_brain.propose(request)
        assert create_result["status"] == "created", f"Expected created, got {create_result}"
        proposal_id = create_result["proposal_id"]

        # Approve
        proposal_path = owner_brain.proposals_pending / f"{proposal_id}.md"
        actual_sha = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
        owner_brain.approve({
            "op": "approve",
            "proposal_id": proposal_id,
            "expected_proposal_sha256": actual_sha,
        })

        # Verify proposal moved
        assert not (owner_brain.proposals_pending / f"{proposal_id}.md").exists()
        assert (owner_brain.proposals_resolved / f"{proposal_id}.md").exists()

    def test_approve_creates_skill_file(self, owner_brain, sample_skill_content):
        """Approved create proposal creates skill file."""
        # Create proposal
        agent_brain = BrainSkills(root=owner_brain.root, is_owner=False)
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "debugging",
            "purpose": "Test",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["LES-test-001"],
        }
        create_result = agent_brain.propose(request)
        assert create_result["status"] == "created", f"Expected created, got {create_result}"
        proposal_id = create_result["proposal_id"]

        # Approve
        proposal_path = owner_brain.proposals_pending / f"{proposal_id}.md"
        actual_sha = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
        owner_brain.approve({
            "op": "approve",
            "proposal_id": proposal_id,
            "expected_proposal_sha256": actual_sha,
        })

        # Verify skill file created
        skill_path = owner_brain.skills_root / "debugging" / "SKILL.md"
        assert skill_path.exists()

    def test_approve_invalidates_stale_proposal(self, owner_brain, sample_skill_content):
        """Stale proposal (skill changed) is rejected."""
        # Create proposal
        agent_brain = BrainSkills(root=owner_brain.root, is_owner=False)
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "debugging",
            "purpose": "Test",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["LES-test-001"],
        }
        create_result = agent_brain.propose(request)
        assert create_result["status"] == "created", f"Expected created, got {create_result}"
        proposal_id = create_result["proposal_id"]

        # Manually change the skill (simulating concurrent edit)
        skill_path = owner_brain.skills_root / "debugging" / "SKILL.md"
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text("modified content")

        # Try to approve with old proposal hash
        proposal_path = owner_brain.proposals_pending / f"{proposal_id}.md"
        actual_sha = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
        result = owner_brain.approve({
            "op": "approve",
            "proposal_id": proposal_id,
            "expected_proposal_sha256": actual_sha,
        })

        assert result["status"] == "rejected"
        assert result["code"] == "STALE_PROPOSAL"

    def test_version_enforcement(self, owner_brain):
        """Skill version increments on approval."""
        # Create skill with version 1
        skill_content_v1 = """---
name: Test Skill
scope: debugging
status: active
version: 1
---

# Version 1

Content v1.
"""
        skill_dir = owner_brain.skills_root / "test"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(skill_content_v1)

        # Create update proposal
        skill_content_v2 = """---
name: Test Skill
scope: debugging
status: active
version: 2
---

# Version 2

Content v2.
"""
        actual_sha = hashlib.sha256(skill_content_v1.encode()).hexdigest()
        agent_brain = BrainSkills(root=owner_brain.root, is_owner=False)
        request: ProposeSkillUpdate = {
            "op": "propose_update",
            "skill_path": "test/SKILL.md",
            "expected_sha256": actual_sha,
            "rationale": "Update to v2",
            "proposed_content": skill_content_v2,
            "evidence_refs": ["LES-test-001"],
        }
        create_result = agent_brain.propose(request)
        assert create_result["status"] == "created", f"Expected created, got {create_result}"
        proposal_id = create_result["proposal_id"]

        # Approve
        proposal_path = owner_brain.proposals_pending / f"{proposal_id}.md"
        actual_proposal_sha = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
        result = owner_brain.approve({
            "op": "approve",
            "proposal_id": proposal_id,
            "expected_proposal_sha256": actual_proposal_sha,
        })

        assert result["status"] == "applied"
        assert result["old_version"] == 1
        assert result["new_version"] == 2

        # Verify skill file has v2
        skill_path = owner_brain.skills_root / "test" / "SKILL.md"
        content = skill_path.read_text()
        assert "version: 2" in content

    def _seed_skill(self, owner_brain, version: int) -> str:
        """Create a skill with the given version; return its SHA."""
        content = f"""---
name: Test Skill
scope: debugging
status: active
version: {version}
---

# Version {version}

Content v{version}.
"""
        skill_dir = owner_brain.skills_root / "test"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content)
        return hashlib.sha256(content.encode()).hexdigest()

    def _propose_and_approve(self, owner_brain, current_sha: str, proposed_version: int) -> SkillResult:
        """Propose an update to the seeded skill and approve it."""
        proposed = f"""---
name: Test Skill
scope: debugging
status: active
version: {proposed_version}
---

# Version {proposed_version}

Content v{proposed_version}.
"""
        agent_brain = BrainSkills(root=owner_brain.root, is_owner=False)
        create_result = agent_brain.propose({
            "op": "propose_update",
            "skill_path": "test/SKILL.md",
            "expected_sha256": current_sha,
            "rationale": "Update",
            "proposed_content": proposed,
            "evidence_refs": ["LES-test-001"],
        })
        assert create_result["status"] == "created", f"Expected created, got {create_result}"
        proposal_id = create_result["proposal_id"]
        proposal_path = owner_brain.proposals_pending / f"{proposal_id}.md"
        actual_sha = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
        return owner_brain.approve({
            "op": "approve",
            "proposal_id": proposal_id,
            "expected_proposal_sha256": actual_sha,
        })

    def test_skipped_version_rejected(self, owner_brain):
        """Update from v1 directly to v3 is rejected as VERSION_MISMATCH."""
        sha = self._seed_skill(owner_brain, 1)
        result = self._propose_and_approve(owner_brain, sha, 3)
        assert result["status"] == "rejected"
        assert "code" in result
        assert result["code"] == "VERSION_MISMATCH"
        # Skill unchanged
        assert "version: 1" in (owner_brain.skills_root / "test" / "SKILL.md").read_text()

    def test_same_version_rejected(self, owner_brain):
        """Update that keeps the same version is rejected."""
        sha = self._seed_skill(owner_brain, 2)
        result = self._propose_and_approve(owner_brain, sha, 2)
        assert result["status"] == "rejected"
        assert "code" in result
        assert result["code"] == "VERSION_MISMATCH"

    def test_version_decrement_rejected(self, owner_brain):
        """Update that lowers the version is rejected."""
        sha = self._seed_skill(owner_brain, 3)
        result = self._propose_and_approve(owner_brain, sha, 2)
        assert result["status"] == "rejected"
        assert "code" in result
        assert result["code"] == "VERSION_MISMATCH"


# ---------------------------------------------------------------------------
# Security — Path boundaries
# ---------------------------------------------------------------------------


class TestSecurityPathBoundaries:
    """Test that skills cannot be created outside skills/."""

    def test_cannot_create_outside_skills(self, brain):
        """Proposal targeting outside skills/ is rejected."""
        request = {
            "op": "propose_create",
            "skill_name": "../../knowledge/test",  # Path traversal
            "purpose": "Test",
            "proposed_content": "content",
            "evidence_refs": ["LES-test-001"],
        }
        result = brain.propose(request)
        assert result["status"] == "rejected"
        assert result["code"] == "INVALID_TARGET_PATH"

    def test_cannot_target_AGENTS_md(self, brain):
        """Proposal targeting AGENTS.md is rejected."""
        request = {
            "op": "propose_update",
            "skill_path": "../AGENTS.md",
            "expected_sha256": "abc123",
            "rationale": "Test",
            "proposed_content": "content",
            "evidence_refs": ["LES-test-001"],
        }
        result = brain.propose(request)
        assert result["status"] == "rejected"
        assert result["code"] == "INVALID_TARGET_PATH"


# ---------------------------------------------------------------------------
# Security — Schema validation
# ---------------------------------------------------------------------------


class TestSecuritySchemaValidation:
    """Test that proposed skills must have valid schema."""

    def test_missing_required_fields(self, owner_brain):
        """Skill without required frontmatter fields is rejected."""
        invalid_content = """---
name: Test
---

Content without scope, status, version.
"""
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "test",
            "purpose": "Test",
            "proposed_content": invalid_content,
            "evidence_refs": ["LES-test-001"],
        }
        # First create the skill
        agent_brain = BrainSkills(root=owner_brain.root, is_owner=False)
        create_result = agent_brain.propose(request)
        assert create_result["status"] == "created", f"Expected created, got {create_result}"
        proposal_id = create_result["proposal_id"]

        # Try to approve
        proposal_path = owner_brain.proposals_pending / f"{proposal_id}.md"
        actual_sha = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
        result = owner_brain.approve({
            "op": "approve",
            "proposal_id": proposal_id,
            "expected_proposal_sha256": actual_sha,
        })
        assert result["status"] == "rejected"
        assert result["code"] == "INVALID_SKILL_SCHEMA"

    def test_reserved_fields_rejected(self, owner_brain):
        """Skill with reserved fields (authority, override_agents) is rejected."""
        invalid_content = """---
name: Test
scope: debugging
status: active
version: 1
authority: constitution
---

Content with reserved field.
"""
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "test",
            "purpose": "Test",
            "proposed_content": invalid_content,
            "evidence_refs": ["LES-test-001"],
        }
        agent_brain = BrainSkills(root=owner_brain.root, is_owner=False)
        create_result = agent_brain.propose(request)
        assert create_result["status"] == "created", f"Expected created, got {create_result}"
        proposal_id = create_result["proposal_id"]

        proposal_path = owner_brain.proposals_pending / f"{proposal_id}.md"
        actual_sha = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
        result = owner_brain.approve({
            "op": "approve",
            "proposal_id": proposal_id,
            "expected_proposal_sha256": actual_sha,
        })
        assert result["status"] == "rejected"
        assert result["code"] == "INVALID_SKILL_SCHEMA"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestLogging:
    """Test that operations are logged to skills.jsonl."""

    def test_proposal_logged(self, brain, sample_skill_content):
        """Proposal creation is logged."""
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "test",
            "purpose": "Test",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["LES-test-001"],
        }
        brain.propose(request)

        log_file = brain.logs_dir / "skills.jsonl"
        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["operation"] == "propose_create"
        assert entry["status"] == "pending"

    def test_approval_logged(self, owner_brain, sample_skill_content):
        """Approval is logged."""
        # Create proposal
        agent_brain = BrainSkills(root=owner_brain.root, is_owner=False)
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "test",
            "purpose": "Test",
            "proposed_content": sample_skill_content,
            "evidence_refs": ["LES-test-001"],
        }
        create_result = agent_brain.propose(request)
        assert create_result["status"] == "created", f"Expected created, got {create_result}"
        proposal_id = create_result["proposal_id"]

        # Approve
        proposal_path = owner_brain.proposals_pending / f"{proposal_id}.md"
        actual_sha = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
        owner_brain.approve({
            "op": "approve",
            "proposal_id": proposal_id,
            "expected_proposal_sha256": actual_sha,
        })

        log_file = owner_brain.logs_dir / "skills.jsonl"
        lines = log_file.read_text().strip().split("\n")
        # Should have at least 2 entries: propose + approve
        assert len(lines) >= 2
        approve_entry = json.loads(lines[-1])
        assert approve_entry["operation"] == "approve"
        assert approve_entry["status"] == "applied"


# ---------------------------------------------------------------------------
# Root isolation
# ---------------------------------------------------------------------------


class TestRootIsolation:
    """Test that operations stay within the configured root."""

    def test_operations_confined_to_root(self, temp_root):
        """All files created are under the temp root."""
        brain = BrainSkills(root=temp_root, is_owner=False)

        # Create a proposal
        request: ProposeSkillCreate = {
            "op": "propose_create",
            "skill_name": "test",
            "purpose": "Test",
            "proposed_content": "content",
            "evidence_refs": ["LES-test-001"],
        }
        result = brain.propose(request)

        # Verify all files are under temp_root
        for path in temp_root.rglob("*"):
            if path.is_file():
                try:
                    path.relative_to(temp_root)
                except ValueError:
                    pytest.fail(f"File {path} is outside temp_root {temp_root}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

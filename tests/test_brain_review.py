"""
Super Brain — Phase 6 Review & Verification Tests

Tests for BrainReviewer deep module + Phase 6 write-lifecycle rules:
- State transitions (Milestone 1): legal + illegal
- Review contract (Milestone 2): discriminated unions, no generic status
- Evidence integrity (Milestone 3): fake/missing/corrupted sources
- Stale-review protection (Milestone 3): expected SHA
- Capability boundaries: review cannot alter content, provenance,
  governance, skills, history, raw
- Lifecycle integration (Milestone 4): verified -> dispute -> write
  corrected -> provisional -> verify (permanent regression test)
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import cast

import pytest

from brain.runtime.review import (
    BrainReviewer,
    DisputeKnowledge,
    VerifyKnowledge,
)
from brain.runtime.write import (
    BrainWriter,
    CreateKnowledge,
    UpdateKnowledge,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_root(tmp_path):
    """Create a temporary brain root for tests."""
    yield tmp_path


@pytest.fixture
def writer(temp_root):
    """BrainWriter bound to the temp root."""
    return BrainWriter(root=temp_root)


@pytest.fixture
def reviewer(temp_root):
    """BrainReviewer bound to the temp root."""
    return BrainReviewer(root=temp_root)


@pytest.fixture
def source_id(writer):
    """Ingest one valid source, return its SRC- ID."""
    src = tmp_source(writer, "# Evidence\n\nPrimary claim.\n")
    result = writer.ingest(str(src))
    return result["source_id"]


def tmp_source(writer: BrainWriter, content: str, suffix: str = ".md") -> Path:
    """Write a temp source file (not ingested)."""
    import tempfile

    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, dir=str(writer.root)
    )
    f.write(content)
    f.close()
    return Path(f.name)


def _create_knowledge(
    writer: BrainWriter,
    path: str,
    title: str,
    source_refs: list[str],
    status: str = "provisional",
) -> str:
    """Create a knowledge page; return its sha256."""
    fm = f"---\ntitle: {title}\nkind: concept\nstatus: {status}\nsource_refs:\n"
    for ref in source_refs:
        fm += f"  - {ref}\n"
    fm += "---\n\nOriginal content.\n"
    req: CreateKnowledge = CreateKnowledge(
        op="create", path=path, content=fm, source_refs=source_refs
    )
    result = writer.write(req)
    assert result["status"] == "created", result
    return result["sha256"]


def _verify_request(
    path: str,
    sha: str,
    refs: list[str],
    rationale: str = "Core claims are supported by reviewed evidence.",
) -> VerifyKnowledge:
    return cast(VerifyKnowledge, {
        "decision": "verify",
        "path": path,
        "expected_sha256": sha,
        "evidence_refs": refs,
        "rationale": rationale,
    })


def _dispute_request(
    path: str,
    sha: str,
    refs: list[str],
    rationale: str = "New credible evidence conflicts with this representation.",
) -> DisputeKnowledge:
    return cast(DisputeKnowledge, {
        "decision": "dispute",
        "path": path,
        "expected_sha256": sha,
        "evidence_refs": refs,
        "rationale": rationale,
    })


def _status_of(root: Path, path: str) -> str:
    """Read the status field from a knowledge page."""
    text = (root / path).read_text(encoding="utf-8")
    for line in text.split("\n"):
        if line.strip().startswith("status:"):
            return line.split(":", 1)[1].strip()
    pytest.fail(f"no status field in {path}")


# ---------------------------------------------------------------------------
# State transitions (blueprint §17)
# ---------------------------------------------------------------------------


def test_provisional_to_verified_succeeds(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    result = reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, [source_id]))

    assert result["status"] == "verified"
    assert result["previous_status"] == "provisional"
    assert result["new_status"] == "verified"
    assert result["review_id"].startswith("BRV-")
    assert _status_of(temp_root, "knowledge/concepts/rag.md") == "verified"


def test_provisional_to_disputed_succeeds(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    result = reviewer.review(_dispute_request("knowledge/concepts/rag.md", sha, [source_id]))

    assert result["status"] == "disputed"
    assert result["previous_status"] == "provisional"
    assert result["new_status"] == "disputed"
    assert _status_of(temp_root, "knowledge/concepts/rag.md") == "disputed"


def test_verified_to_disputed_succeeds(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, [source_id]))
    verified_sha = hashlib.sha256((temp_root / "knowledge/concepts/rag.md").read_bytes()).hexdigest()

    result = reviewer.review(_dispute_request("knowledge/concepts/rag.md", verified_sha, [source_id]))

    assert result["status"] == "disputed"
    assert result["previous_status"] == "verified"
    assert _status_of(temp_root, "knowledge/concepts/rag.md") == "disputed"


def test_disputed_to_verified_rejected(temp_root, writer, reviewer, source_id) -> None:
    """disputed -> verified directly is illegal: changed content must be re-reviewed."""
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    reviewer.review(_dispute_request("knowledge/concepts/rag.md", sha, [source_id]))
    disputed_sha = hashlib.sha256((temp_root / "knowledge/concepts/rag.md").read_bytes()).hexdigest()

    result = reviewer.review(_verify_request("knowledge/concepts/rag.md", disputed_sha, [source_id]))

    assert result["status"] == "rejected"
    assert result["code"] == "INVALID_TRANSITION"


def test_verified_to_verified_rejected(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, [source_id]))
    verified_sha = hashlib.sha256((temp_root / "knowledge/concepts/rag.md").read_bytes()).hexdigest()

    result = reviewer.review(_verify_request("knowledge/concepts/rag.md", verified_sha, [source_id]))

    assert result["status"] == "rejected"
    assert result["code"] == "INVALID_TRANSITION"


def test_disputed_to_disputed_rejected(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    reviewer.review(_dispute_request("knowledge/concepts/rag.md", sha, [source_id]))
    disputed_sha = hashlib.sha256((temp_root / "knowledge/concepts/rag.md").read_bytes()).hexdigest()

    result = reviewer.review(_dispute_request("knowledge/concepts/rag.md", disputed_sha, [source_id]))

    assert result["status"] == "rejected"
    assert result["code"] == "INVALID_TRANSITION"


# ---------------------------------------------------------------------------
# Evidence integrity (blueprint §17)
# ---------------------------------------------------------------------------


def test_fake_source_rejected(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    result = reviewer.review(
        _verify_request("knowledge/concepts/rag.md", sha, ["SRC-000000000000"])
    )
    assert result["status"] == "rejected"
    assert result["code"] == "SOURCE_NOT_FOUND"


def test_missing_source_rejected(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    result = reviewer.review(
        _verify_request("knowledge/concepts/rag.md", sha, ["SRC-deadbeef1234"])
    )
    assert result["status"] == "rejected"
    assert result["code"] == "SOURCE_NOT_FOUND"


def test_corrupted_source_rejected(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    # Tamper with the raw source: hash must no longer match metadata
    source_md = temp_root / "raw" / "sources" / source_id / "source.md"
    source_md.write_text(source_md.read_text() + "\n# Tampered\n")

    result = reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, [source_id]))

    assert result["status"] == "rejected"
    assert result["code"] == "SOURCE_INTEGRITY_FAILED"


def test_valid_source_accepted(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    result = reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, [source_id]))
    assert result["status"] == "verified"


def test_multiple_valid_sources_accepted(temp_root, writer, reviewer, source_id) -> None:
    src2 = tmp_source(writer, "# Evidence B\n\nCorroborating claim.\n")
    s2 = writer.ingest(str(src2))["source_id"]
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id, s2])
    result = reviewer.review(
        _verify_request("knowledge/concepts/rag.md", sha, [source_id, s2])
    )
    assert result["status"] == "verified"


def test_empty_evidence_rejected(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    result = reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, []))
    assert result["status"] == "rejected"
    assert result["code"] == "MISSING_EVIDENCE"


def test_non_src_evidence_rejected(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    result = reviewer.review(
        _verify_request("knowledge/concepts/rag.md", sha, ["LES-test-001"])
    )
    assert result["status"] == "rejected"
    assert result["code"] == "INVALID_EVIDENCE"


# ---------------------------------------------------------------------------
# Stale-review protection (blueprint §12)
# ---------------------------------------------------------------------------


def test_correct_expected_hash_accepted(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    result = reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, [source_id]))
    assert result["status"] == "verified"


def test_stale_expected_hash_rejected(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    # Page changes after Pi read it (another write)
    stale = _create_knowledge(writer, "knowledge/concepts/other.md", "Other", [source_id])
    req: UpdateKnowledge = cast(UpdateKnowledge, {
        "op": "update",
        "path": "knowledge/concepts/rag.md",
        "content": "---\ntitle: RAG\nkind: concept\nstatus: provisional\n"
                   f"source_refs:\n  - {source_id}\n---\n\nRevised content.\n",
        "source_refs": [source_id],
        "expected_sha256": stale,  # wrong sha on purpose? no — use correct flow below
    })
    # Simulate: someone updated rag.md after our read
    _update_page(writer, "knowledge/concepts/rag.md", source_id)

    result = reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, [source_id]))

    assert result["status"] == "rejected"
    assert result["code"] == "STALE_REVIEW"


def _update_page(writer: BrainWriter, path: str, source_id: str) -> None:
    """Update a page through the writer (as another actor would)."""
    current = hashlib.sha256((writer.root / path).read_bytes()).hexdigest()
    req: UpdateKnowledge = cast(UpdateKnowledge, {
        "op": "update",
        "path": path,
        "content": "---\ntitle: RAG\nkind: concept\nstatus: provisional\n"
                   f"source_refs:\n  - {source_id}\n---\n\nContent changed by someone else.\n",
        "source_refs": [source_id],
        "expected_sha256": current,
    })
    result = writer.write(req)
    assert result["status"] == "updated", result


def test_missing_expected_sha_rejected(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    req = _verify_request("knowledge/concepts/rag.md", sha, [source_id])
    req["expected_sha256"] = ""
    result = reviewer.review(req)
    assert result["status"] == "rejected"
    assert result["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# Capability boundaries: review changes state ONLY (blueprint §13, §17)
# ---------------------------------------------------------------------------


def test_review_cannot_alter_body(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    before = (temp_root / "knowledge/concepts/rag.md").read_text()
    reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, [source_id]))
    after = (temp_root / "knowledge/concepts/rag.md").read_text()

    # Only the status line may differ
    assert after.replace("status: verified", "status: provisional") == before


def test_review_cannot_alter_source_refs(temp_root, writer, reviewer, source_id) -> None:
    """Review must not append evidence refs to the page.

    Evidence refs used for verification must already be in the page's
    source_refs (enforced by the provenance-binding check). This test
    verifies that review does not silently add new refs to the page.
    """
    src2 = tmp_source(writer, "# Source B\n\nSecond claim.\n")
    s2 = writer.ingest(str(src2))["source_id"]
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id, s2])
    reviewer.review(
        _verify_request("knowledge/concepts/rag.md", sha, [source_id, s2])
    )
    text = (temp_root / "knowledge/concepts/rag.md").read_text()
    # Both refs should be present (they were in the original source_refs)
    assert f"- {source_id}" in text
    assert f"- {s2}" in text
    # But review must not have added any extra refs beyond what was there
    import re
    refs_in_text = re.findall(r"- (SRC-\w+)", text)
    assert set(refs_in_text) == {source_id, s2}


def test_review_cannot_touch_constitution(temp_root, reviewer) -> None:
    path = "brain/constitution.md"
    sha = hashlib.sha256((temp_root / path).read_bytes()).hexdigest() if (temp_root / path).exists() else "x"
    if not (temp_root / path).exists():
        (temp_root / "brain").mkdir(parents=True)
        (temp_root / path).write_text("---\nstatus: core\n---\n\nConstitution.\n")
        sha = hashlib.sha256((temp_root / path).read_bytes()).hexdigest()
    result = reviewer.review(_verify_request(path, sha, ["SRC-whatever"]))
    assert result["status"] == "rejected"
    assert result["code"] == "UNSAFE_PATH"


def test_review_cannot_touch_skills(temp_root, reviewer) -> None:
    (temp_root / "skills" / "debugging").mkdir(parents=True)
    skill = temp_root / "skills" / "debugging" / "SKILL.md"
    skill.write_text("---\nname: debugging\nstatus: active\n---\n\nProcedure.\n")
    sha = hashlib.sha256(skill.read_bytes()).hexdigest()
    result = reviewer.review(_verify_request("skills/debugging/SKILL.md", sha, ["SRC-whatever"]))
    assert result["status"] == "rejected"
    assert result["code"] == "UNSAFE_PATH"


def test_review_cannot_touch_history(temp_root, reviewer) -> None:
    (temp_root / "history" / "decisions").mkdir(parents=True)
    dec = temp_root / "history" / "decisions" / "BRN-001.md"
    dec.write_text("---\nid: BRN-001\nstatus: accepted\n---\n\nDecision.\n")
    sha = hashlib.sha256(dec.read_bytes()).hexdigest()
    result = reviewer.review(_verify_request("history/decisions/BRN-001.md", sha, ["SRC-whatever"]))
    assert result["status"] == "rejected"
    assert result["code"] == "UNSAFE_PATH"


def test_review_cannot_touch_raw(temp_root, reviewer, writer, source_id) -> None:
    source_md = temp_root / "raw" / "sources" / source_id / "source.md"
    sha = hashlib.sha256(source_md.read_bytes()).hexdigest()
    result = reviewer.review(_verify_request(f"raw/sources/{source_id}/source.md", sha, [source_id]))
    assert result["status"] == "rejected"
    assert result["code"] == "UNSAFE_PATH"


def test_path_traversal_blocked(temp_root, reviewer, source_id) -> None:
    result = reviewer.review(
        _verify_request("knowledge/../brain/constitution.md", "abc", [source_id])
    )
    assert result["status"] == "rejected"
    assert result["code"] == "UNSAFE_PATH"


def test_symlink_escape_blocked(temp_root, writer, reviewer, source_id) -> None:
    """A knowledge/ symlink pointing outside is rejected."""
    outside = temp_root / "outside-target.md"
    outside.write_text("---\nstatus: provisional\n---\n\nSecret.\n")
    (temp_root / "knowledge").mkdir(parents=True, exist_ok=True)
    link = temp_root / "knowledge" / "escape.md"
    link.symlink_to(outside)
    sha = hashlib.sha256(outside.read_bytes()).hexdigest()
    result = reviewer.review(_verify_request("knowledge/escape.md", sha, [source_id]))
    assert result["status"] == "rejected"
    assert result["code"] == "UNSAFE_PATH"


def test_target_not_found(temp_root, reviewer, source_id) -> None:
    result = reviewer.review(
        _verify_request("knowledge/concepts/nope.md", "abc", [source_id])
    )
    assert result["status"] == "rejected"
    assert result["code"] == "TARGET_NOT_FOUND"


# ---------------------------------------------------------------------------
# Lifecycle integration — permanent regression test (blueprint §17)
# ---------------------------------------------------------------------------


def test_full_lifecycle_verified_dispute_write_verify(temp_root, writer, reviewer, source_id) -> None:
    """verified -> dispute -> write corrected -> provisional -> verify."""
    # Build verified knowledge
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    r = reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, [source_id]))
    assert r["status"] == "verified"
    assert _status_of(temp_root, "knowledge/concepts/rag.md") == "verified"

    # Contradictory evidence arrives -> dispute
    verified_sha = hashlib.sha256((temp_root / "knowledge/concepts/rag.md").read_bytes()).hexdigest()
    src2 = tmp_source(writer, "# Contradiction\n\nRAG claims are overstated.\n")
    s2 = writer.ingest(str(src2))["source_id"]
    r = reviewer.review(_dispute_request("knowledge/concepts/rag.md", verified_sha, [s2]))
    assert r["status"] == "disputed"
    assert _status_of(temp_root, "knowledge/concepts/rag.md") == "disputed"

    # Correct the page -> auto-reset to provisional
    disputed_sha = hashlib.sha256((temp_root / "knowledge/concepts/rag.md").read_bytes()).hexdigest()
    content = (
        "---\ntitle: RAG\nkind: concept\nstatus: provisional\n"
        f"source_refs:\n  - {source_id}\n  - {s2}\n---\n\n"
        "Corrected content accounting for both sources.\n"
    )
    req: UpdateKnowledge = cast(UpdateKnowledge, {
        "op": "update",
        "path": "knowledge/concepts/rag.md",
        "content": content,
        "source_refs": [source_id, s2],
        "expected_sha256": disputed_sha,
    })
    r = writer.write(req)
    assert r["status"] == "updated", r
    assert _status_of(temp_root, "knowledge/concepts/rag.md") == "provisional"

    # Re-review -> verified again
    corrected_sha = hashlib.sha256((temp_root / "knowledge/concepts/rag.md").read_bytes()).hexdigest()
    r = reviewer.review(_verify_request("knowledge/concepts/rag.md", corrected_sha, [source_id, s2]))
    assert r["status"] == "verified"
    assert _status_of(temp_root, "knowledge/concepts/rag.md") == "verified"


# ---------------------------------------------------------------------------
# Write lifecycle rules (Phase 6 updates to Phase 2 behavior)
# ---------------------------------------------------------------------------


def test_verified_target_update_rejected_even_if_claims_verified(
    temp_root, writer, reviewer, source_id
) -> None:
    """The verified->verified carve-out is closed: content lock is absolute."""
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, [source_id]))
    verified_sha = hashlib.sha256((temp_root / "knowledge/concepts/rag.md").read_bytes()).hexdigest()

    content = (
        "---\ntitle: RAG\nkind: concept\nstatus: verified\n"
        f"source_refs:\n  - {source_id}\n---\n\nSilent rewrite attempt.\n"
    )
    req: UpdateKnowledge = cast(UpdateKnowledge, {
        "op": "update",
        "path": "knowledge/concepts/rag.md",
        "content": content,
        "source_refs": [source_id],
        "expected_sha256": verified_sha,
    })
    result = writer.write(req)
    assert result["status"] == "rejected"
    assert result["code"] == "VERIFIED_TARGET_REQUIRES_REVIEW"
    # Content untouched
    assert "Silent rewrite attempt" not in (temp_root / "knowledge/concepts/rag.md").read_text()


def test_disputed_target_update_auto_resets_to_provisional(
    temp_root, writer, reviewer, source_id
) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    reviewer.review(_dispute_request("knowledge/concepts/rag.md", sha, [source_id]))
    disputed_sha = hashlib.sha256((temp_root / "knowledge/concepts/rag.md").read_bytes()).hexdigest()

    content = (
        "---\ntitle: RAG\nkind: concept\nstatus: provisional\n"
        f"source_refs:\n  - {source_id}\n---\n\nCorrected content.\n"
    )
    req: UpdateKnowledge = cast(UpdateKnowledge, {
        "op": "update",
        "path": "knowledge/concepts/rag.md",
        "content": content,
        "source_refs": [source_id],
        "expected_sha256": disputed_sha,
    })
    result = writer.write(req)
    assert result["status"] == "updated", result
    assert _status_of(temp_root, "knowledge/concepts/rag.md") == "provisional"


def test_provisional_target_claiming_verified_rejected(
    temp_root, writer, reviewer, source_id
) -> None:
    """Self-verification guard: an update may not claim verified."""
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    content = (
        "---\ntitle: RAG\nkind: concept\nstatus: verified\n"
        f"source_refs:\n  - {source_id}\n---\n\nSelf-verified content.\n"
    )
    req: UpdateKnowledge = cast(UpdateKnowledge, {
        "op": "update",
        "path": "knowledge/concepts/rag.md",
        "content": content,
        "source_refs": [source_id],
        "expected_sha256": sha,
    })
    result = writer.write(req)
    assert result["status"] == "rejected"
    assert result["code"] == "INVALID_FRONTMATTER"
    assert _status_of(temp_root, "knowledge/concepts/rag.md") == "provisional"


def test_create_with_disputed_rejected(temp_root, writer, source_id) -> None:
    fm = (
        "---\ntitle: New Concept\nkind: concept\nstatus: disputed\n"
        f"source_refs:\n  - {source_id}\n---\n\nContent.\n"
    )
    req: CreateKnowledge = CreateKnowledge(
        op="create", path="knowledge/concepts/new.md", content=fm, source_refs=[source_id]
    )
    result = writer.write(req)
    assert result["status"] == "rejected"
    assert result["code"] == "INVALID_FRONTMATTER"


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


def test_review_logged(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, [source_id]))

    log_path = temp_root / "logs" / "reviews.jsonl"
    assert log_path.exists()
    entries = [json.loads(l) for l in log_path.read_text().strip().split("\n") if l.strip()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["review_id"].startswith("BRV-")
    assert entry["decision"] == "verify"
    assert entry["path"] == "knowledge/concepts/rag.md"
    assert entry["previous_status"] == "provisional"
    assert entry["new_status"] == "verified"
    assert entry["evidence_refs"] == [source_id]
    assert "supported" in entry["rationale"]
    # No full source content copied into logs
    assert "Primary claim" not in json.dumps(entry)
    assert len(entry["content_sha256"]) == 64


def test_rejection_logged(temp_root, writer, reviewer, source_id) -> None:
    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    reviewer.review(_verify_request("knowledge/concepts/rag.md", "stale-sha", [source_id]))

    log_path = temp_root / "logs" / "reviews.jsonl"
    entries = [json.loads(l) for l in log_path.read_text().strip().split("\n") if l.strip()]
    assert entries[-1]["error"].startswith("STALE_REVIEW:")


def test_ingest_records_authority(writer) -> None:
    src = tmp_source(writer, "# Official standard\n\nNormative text.\n")
    result = writer.ingest(str(src), authority="primary")
    assert result["status"] == "created"
    meta = (writer.root / "raw" / "sources" / result["source_id"] / "metadata.yaml").read_text()
    assert "authority: primary" in meta


def test_ingest_default_authority_unknown(writer) -> None:
    src = tmp_source(writer, "# Forum post\n\nOpinion.\n")
    result = writer.ingest(str(src))
    meta = (writer.root / "raw" / "sources" / result["source_id"] / "metadata.yaml").read_text()
    assert "authority: unknown" in meta


def test_ingest_invalid_authority_rejected(writer) -> None:
    src = tmp_source(writer, "# Source\n\nContent.\n")
    with pytest.raises(ValueError, match="INVALID_AUTHORITY"):
        writer.ingest(str(src), authority="0.93")


def test_ingest_existing_refreshes_authority(writer) -> None:
    src = tmp_source(writer, "# Source\n\nContent.\n")
    r1 = writer.ingest(str(src))
    r2 = writer.ingest(str(src), authority="primary")
    assert r2["status"] == "existing"
    meta = (writer.root / "raw" / "sources" / r1["source_id"] / "metadata.yaml").read_text()
    assert "authority: primary" in meta


# ---------------------------------------------------------------------------
# Query exposes epistemic status (Milestone 1)
# ---------------------------------------------------------------------------


def test_query_exposes_status(temp_root, writer, reviewer, source_id) -> None:
    from brain.runtime.search import BrainSearch

    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])
    reviewer.review(_verify_request("knowledge/concepts/rag.md", sha, [source_id]))

    engine = BrainSearch(root=temp_root)
    response = engine.query("RAG retrieval", top_k=5)
    assert response.results
    hit = next(r for r in response.results if "rag" in r.path)
    assert hit.status == "verified"


if __name__ == "__main__":
    import pytest as _pytest

    _pytest.main([__file__, "-v"])

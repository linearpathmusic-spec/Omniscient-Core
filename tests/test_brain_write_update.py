"""
Super Brain — UPDATE Tests (Milestone 4)

Verify safe knowledge revision invariants:
- valid update succeeds
- nonexistent target rejected
- correct expected SHA accepted
- stale SHA rejected
- previous source refs preserved
- dropping old provenance rejected
- verified target protected
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

BRAIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN_ROOT))

TEST_ROOT = Path(tempfile.mkdtemp(prefix="sb-test-"))

from brain.runtime.write import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    BrainWriter,
    CreateKnowledge,
    UpdateKnowledge,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(content: str, suffix: str = ".md") -> Path:
    """Create a temporary source file."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, dir=str(TEST_ROOT)
    )
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


def _cleanup(root: Path) -> None:
    """Remove raw/sources, knowledge, and logs created during tests."""
    for d in ["raw", "knowledge", "logs"]:
        p = root / d
        if p.exists():
            shutil.rmtree(p)


def _ingest_source(writer: BrainWriter, content: str) -> str:
    """Ingest a source and return its source_id."""
    src = _make_source(content, ".md")
    result = writer.ingest(str(src))
    assert result["status"] == "created"
    return result["source_id"]


def _create_knowledge(
    writer: BrainWriter, path: str, title: str, source_refs: list[str]
) -> str:
    """Create a knowledge page, return its sha256."""
    fm = f"---\ntitle: {title}\nkind: concept\nstatus: provisional\nsource_refs:\n"
    for ref in source_refs:
        fm += f"  - {ref}\n"
    fm += "---\n\nOriginal content.\n"
    req: CreateKnowledge = CreateKnowledge(
        op="create", path=path, content=fm, source_refs=source_refs
    )
    result = writer.write(req)
    assert result["status"] == "created", result
    return result["sha256"]


def _update_request(
    path: str,
    expected_sha256: str,
    source_refs: list[str],
    title: str = "Updated Title",
) -> UpdateKnowledge:
    """Build an UPDATE request with revised content."""
    fm = f"---\ntitle: {title}\nkind: concept\nstatus: provisional\nsource_refs:\n"
    for ref in source_refs:
        fm += f"  - {ref}\n"
    fm += "---\n\nRevised content with new evidence.\n"
    return UpdateKnowledge(
        op="update",
        path=path,
        content=fm,
        source_refs=source_refs,
        expected_sha256=expected_sha256,
    )


# ---------------------------------------------------------------------------
# UPDATE tests
# ---------------------------------------------------------------------------


def test_valid_update_succeeds() -> None:
    """A valid update with matching expected SHA succeeds."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source A\n\nFirst evidence.\n")
        s2 = _ingest_source(writer, "# Source B\n\nSecond evidence.\n")

        old_sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [s1])

        req = _update_request("knowledge/concepts/rag.md", old_sha, [s1, s2])
        result = writer.write(req)

        assert result["status"] == "updated"
        assert result["path"] == "knowledge/concepts/rag.md"
        assert result["old_sha256"] == old_sha
        assert result["new_sha256"] != old_sha
        assert len(result["new_sha256"]) == 64

        # File content actually revised
        content = (TEST_ROOT / result["path"]).read_text()
        assert "Revised content with new evidence" in content
    finally:
        _cleanup(TEST_ROOT)


def test_nonexistent_target_rejected() -> None:
    """Updating a nonexistent target is rejected."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source\n\nEvidence.\n")

        req = _update_request("knowledge/concepts/does-not-exist.md", "abc123", [s1])
        result = writer.write(req)

        assert result["status"] == "rejected"
        assert result["code"] == "TARGET_NOT_FOUND"
    finally:
        _cleanup(TEST_ROOT)


def test_correct_expected_sha_accepted() -> None:
    """Update with the correct expected SHA is accepted."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source\n\nEvidence.\n")

        old_sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [s1])

        req = _update_request("knowledge/concepts/rag.md", old_sha, [s1])
        result = writer.write(req)

        assert result["status"] == "updated"
        assert result["old_sha256"] == old_sha
    finally:
        _cleanup(TEST_ROOT)


def test_stale_sha_rejected() -> None:
    """Update with a stale expected SHA is rejected."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source\n\nEvidence.\n")

        old_sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [s1])

        # Someone else writes first (simulate concurrent update)
        req1 = _update_request("knowledge/concepts/rag.md", old_sha, [s1], title="First Change")
        result1 = writer.write(req1)
        assert result1["status"] == "updated"

        # Now try updating with the ORIGINAL (now stale) sha
        req2 = _update_request("knowledge/concepts/rag.md", old_sha, [s1], title="Second Change")
        result2 = writer.write(req2)

        assert result2["status"] == "rejected"
        assert result2["code"] == "STALE_WRITE"
    finally:
        _cleanup(TEST_ROOT)


def test_previous_source_refs_preserved() -> None:
    """Old source refs are preserved in the updated file."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source A\n\nEvidence A.\n")
        s2 = _ingest_source(writer, "# Source B\n\nEvidence B.\n")

        old_sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [s1])

        req = _update_request("knowledge/concepts/rag.md", old_sha, [s1, s2])
        result = writer.write(req)
        assert result["status"] == "updated"

        content = (TEST_ROOT / result["path"]).read_text()
        assert s1 in content  # old ref preserved
        assert s2 in content  # new ref added
    finally:
        _cleanup(TEST_ROOT)


def test_dropping_old_provenance_rejected() -> None:
    """Removing an old source ref is rejected."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source A\n\nEvidence A.\n")
        s2 = _ingest_source(writer, "# Source B\n\nEvidence B.\n")

        old_sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [s1, s2])

        # Attempt to drop s1 from provenance
        req = _update_request("knowledge/concepts/rag.md", old_sha, [s2])
        result = writer.write(req)

        assert result["status"] == "rejected"
        assert result["code"] == "PROVENANCE_REMOVED"
    finally:
        _cleanup(TEST_ROOT)


def test_verified_target_protected() -> None:
    """A verified knowledge page cannot be autonomously rewritten to provisional."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source\n\nEvidence.\n")

        # Create provisional, then manually mark verified (simulating owner review)
        _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [s1])
        target = TEST_ROOT / "knowledge" / "concepts" / "rag.md"
        content = target.read_text().replace(
            "status: provisional", "status: verified"
        )
        target.write_text(content)

        # Pi reads the verified file: current sha + verified status
        import hashlib
        verified_sha = hashlib.sha256(target.read_bytes()).hexdigest()

        # Attempt autonomous update with the verified file's sha
        req = _update_request("knowledge/concepts/rag.md", verified_sha, [s1])
        result = writer.write(req)

        assert result["status"] == "rejected"
        assert result["code"] == "VERIFIED_TARGET_REQUIRES_REVIEW"

        # Original verified file untouched
        assert "status: verified" in target.read_text()
    finally:
        _cleanup(TEST_ROOT)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def run_all() -> int:
    """Run all M4 UPDATE tests. Returns 0 on success."""
    tests = [
        test_valid_update_succeeds,
        test_nonexistent_target_rejected,
        test_correct_expected_sha_accepted,
        test_stale_sha_rejected,
        test_previous_source_refs_preserved,
        test_dropping_old_provenance_rejected,
        test_verified_target_protected,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {test.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {test.__name__}: {e}")

    print(f"\nM4 UPDATE Tests: {passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all())

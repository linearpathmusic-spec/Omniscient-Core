"""
Super Brain — CREATE Tests (Milestone 3)

Verify safe knowledge creation invariants:
- valid knowledge create succeeds
- existing target rejected
- duplicate normalized title rejected
- missing source refs rejected
- nonexistent source ref rejected
- invalid frontmatter rejected
- unsafe path rejected
- verified status rejected
"""

from __future__ import annotations

import hashlib
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
    WriteCreated,
    WriteRejected,
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


def _make_create_request(
    path: str,
    title: str,
    kind: str = "concept",
    source_refs: list[str] | None = None,
    status: str = "provisional",
) -> CreateKnowledge:
    """Build a valid CREATE request.

    Path is relative to knowledge/ (e.g., 'concepts/test.md').
    """
    fm = f"---\ntitle: {title}\nkind: {kind}\nstatus: {status}\n"
    if source_refs:
        fm += "source_refs:\n"
        for ref in source_refs:
            fm += f"  - {ref}\n"
    fm += "---\n\nKnowledge content.\n"
    return CreateKnowledge(
        op="create",
        path=path,
        content=fm,
        source_refs=source_refs or ["SRC-abc123def456"],
    )


# ---------------------------------------------------------------------------
# CREATE tests
# ---------------------------------------------------------------------------


def test_valid_create_succeeds() -> None:
    """A valid knowledge create succeeds."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)

        # Create a source first
        src = _make_source("# Source\n\nEvidence.\n", ".md")
        ingest_result = writer.ingest(str(src))
        assert ingest_result["status"] == "created"
        source_id = ingest_result["source_id"]

        # Create knowledge from source
        request = _make_create_request(
            path="knowledge/concepts/test-concept.md",
            title="Test Concept",
            source_refs=[source_id],
        )
        result = writer.write(request)

        assert isinstance(result, dict)
        assert result["status"] == "created"
        assert result["path"] == "knowledge/concepts/test-concept.md"
        assert "sha256" in result
        assert len(result["sha256"]) == 64

        # Verify file exists
        target = TEST_ROOT / result["path"]
        assert target.exists()

        # Verify frontmatter
        content = target.read_text()
        assert "---" in content
        assert "title: Test Concept" in content
        assert "kind: concept" in content
        assert f"- {source_id}" in content
    finally:
        _cleanup(TEST_ROOT)


def test_existing_target_rejected() -> None:
    """Creating knowledge to an existing path is rejected."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)

        # Create a source
        src = _make_source("# Source\n\nEvidence.\n", ".md")
        ingest_result = writer.ingest(str(src))
        source_id = ingest_result["source_id"]

        # First create succeeds
        req1 = _make_create_request(
            path="knowledge/concepts/existing.md",
            title="Existing",
            source_refs=[source_id],
        )
        result1 = writer.write(req1)
        assert result1["status"] == "created"

        # Second create to same path fails
        req2 = _make_create_request(
            path="knowledge/concepts/existing.md",
            title="Duplicate",
            source_refs=[source_id],
        )
        result2 = writer.write(req2)

        assert result2["status"] == "rejected"
        assert result2["code"] == "TARGET_EXISTS"
    finally:
        _cleanup(TEST_ROOT)


def test_duplicate_normalized_title_rejected() -> None:
    """Duplicate normalized title (case-insensitive, whitespace-normalized) is rejected."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)

        # Create a source
        src = _make_source("# Source\n\nEvidence.\n", ".md")
        ingest_result = writer.ingest(str(src))
        source_id = ingest_result["source_id"]

        # First create with title "Test Concept"
        req1 = _make_create_request(
            path="knowledge/concepts/test-concept.md",
            title="Test Concept",
            source_refs=[source_id],
        )
        result1 = writer.write(req1)
        assert result1["status"] == "created"

        # Second create with title "test  concept" (different case, extra space)
        req2 = _make_create_request(
            path="knowledge/concepts/other.md",
            title="test  concept",
            source_refs=[source_id],
        )
        result2 = writer.write(req2)

        assert result2["status"] == "rejected"
        assert result2["code"] == "DUPLICATE_TITLE"
    finally:
        _cleanup(TEST_ROOT)


def test_missing_source_refs_rejected() -> None:
    """CREATE whose frontmatter lacks source_refs is rejected as invalid schema."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)

        req = CreateKnowledge(
            op="create",
            path="knowledge/concepts/no-refs.md",
            content="---\ntitle: No Refs\nkind: concept\nstatus: provisional\n---\n\nContent.\n",
            source_refs=[],
        )
        result = writer.write(req)

        # source_refs is a required frontmatter field; absence is a schema error
        assert result["status"] == "rejected"
        assert result["code"] == "INVALID_FRONTMATTER"
    finally:
        _cleanup(TEST_ROOT)


def test_empty_source_refs_rejected() -> None:
    """CREATE with an empty source_refs list is rejected as missing provenance."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)

        req = CreateKnowledge(
            op="create",
            path="knowledge/concepts/empty-refs.md",
            content="---\ntitle: Empty Refs\nkind: concept\nstatus: provisional\nsource_refs: []\n---\n\nContent.\n",
            source_refs=[],
        )
        result = writer.write(req)

        assert result["status"] == "rejected"
        assert result["code"] == "MISSING_PROVENANCE"
    finally:
        _cleanup(TEST_ROOT)


def test_nonexistent_source_ref_rejected() -> None:
    """CREATE with a nonexistent source ref is rejected."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)

        req = CreateKnowledge(
            op="create",
            path="knowledge/concepts/bad-ref.md",
            content="---\ntitle: Bad Ref\nkind: concept\nstatus: provisional\nsource_refs:\n  - SRC-nonexistent123\n---\n\nContent.\n",
            source_refs=["SRC-nonexistent123"],
        )
        result = writer.write(req)

        assert result["status"] == "rejected"
        assert result["code"] == "SOURCE_NOT_FOUND"
    finally:
        _cleanup(TEST_ROOT)


def test_invalid_frontmatter_rejected() -> None:
    """CREATE with invalid frontmatter is rejected."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)

        # Missing required field
        req = CreateKnowledge(
            op="create",
            path="knowledge/concepts/no-kind.md",
            content="---\ntitle: No Kind\nstatus: provisional\nsource_refs:\n  - SRC-abc123\n---\n\nContent.\n",
            source_refs=["SRC-abc123"],
        )
        result = writer.write(req)
        assert result["status"] == "rejected"
        assert result["code"] == "INVALID_FRONTMATTER"

        # Invalid kind
        req2 = CreateKnowledge(
            op="create",
            path="knowledge/concepts/bad-kind.md",
            content="---\ntitle: Bad Kind\nkind: invalid_kind\nstatus: provisional\nsource_refs:\n  - SRC-abc123\n---\n\nContent.\n",
            source_refs=["SRC-abc123"],
        )
        result2 = writer.write(req2)
        assert result2["status"] == "rejected"
        assert result2["code"] == "INVALID_FRONTMATTER"
    finally:
        _cleanup(TEST_ROOT)


def test_unsafe_path_rejected() -> None:
    """CREATE with path outside knowledge/ is rejected."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)

        src = _make_source("# Source\n\nEvidence.\n", ".md")
        ingest_result = writer.ingest(str(src))
        source_id = ingest_result["source_id"]

        req = CreateKnowledge(
            op="create",
            path="../brain/constitution.md",
            content=f"---\ntitle: Escape\nkind: concept\nstatus: provisional\nsource_refs:\n  - {source_id}\n---\n\nContent.\n",
            source_refs=[source_id],
        )
        result = writer.write(req)

        assert result["status"] == "rejected"
        assert result["code"] == "UNSAFE_PATH"
    finally:
        _cleanup(TEST_ROOT)


def test_verified_status_rejected() -> None:
    """CREATE with status 'verified' is rejected (only 'provisional' allowed)."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)

        req = CreateKnowledge(
            op="create",
            path="knowledge/concepts/verified.md",
            content="---\ntitle: Verified\nkind: concept\nstatus: verified\nsource_refs:\n  - SRC-abc123\n---\n\nContent.\n",
            source_refs=["SRC-abc123"],
        )
        result = writer.write(req)

        assert result["status"] == "rejected"
        assert result["code"] == "INVALID_FRONTMATTER"
    finally:
        _cleanup(TEST_ROOT)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def run_all() -> int:
    """Run all M3 CREATE tests. Returns 0 on success."""
    tests = [
        test_valid_create_succeeds,
        test_existing_target_rejected,
        test_duplicate_normalized_title_rejected,
        test_missing_source_refs_rejected,
        test_empty_source_refs_rejected,
        test_nonexistent_source_ref_rejected,
        test_invalid_frontmatter_rejected,
        test_unsafe_path_rejected,
        test_verified_status_rejected,
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

    print(f"\nM3 CREATE Tests: {passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all())

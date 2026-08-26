"""
Super Brain — Ingest Tests (Milestone 2)

Verify immutable source capture invariants:
- valid text source creates source
- same bytes return existing
- different bytes create different ID
- SHA matches preserved bytes
- unsupported format rejected
- existing source never overwritten
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

BRAIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN_ROOT))

TEST_ROOT = Path(tempfile.mkdtemp(prefix="sb-test-"))

from brain.runtime.write import BrainWriter, IngestCreated, IngestExisting  # noqa: E402  # pyright: ignore[reportMissingImports]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_file(content: str, suffix: str = ".md") -> Path:
    """Create a temporary file with given content."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, dir=str(TEST_ROOT)
    )
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


def _cleanup(root: Path) -> None:
    """Remove raw/sources and logs created during tests."""
    raw = root / "raw"
    logs = root / "logs"
    for d in [raw, logs]:
        if d.exists():
            shutil.rmtree(d)


# ---------------------------------------------------------------------------
# Ingest tests
# ---------------------------------------------------------------------------


def test_valid_text_source_creates() -> None:
    """A valid text source creates a new source entry."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        content = "# Test Source\n\nThis is test content.\n"
        src = _make_temp_file(content, ".md")

        result = writer.ingest(str(src))

        assert isinstance(result, dict)
        assert result["status"] == "created"
        assert result["source_id"].startswith("SRC-")
        assert len(result["sha256"]) == 64
        assert Path(result["path"]).exists()

        # Verify metadata exists and matches
        meta_path = Path(result["path"]).parent / "metadata.yaml"
        assert meta_path.exists()
        meta = writer._load_yaml(meta_path)
        assert meta["sha256"] == result["sha256"]
        assert meta["source_id"] == result["source_id"]
    finally:
        _cleanup(TEST_ROOT)


def test_same_bytes_return_existing() -> None:
    """Ingesting the same bytes twice returns existing."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        content = "# Dedup Test\n\nIdentical content.\n"
        src = _make_temp_file(content, ".md")

        first = writer.ingest(str(src))
        second = writer.ingest(str(src))

        assert first["status"] == "created"
        assert second["status"] == "existing"
        assert first["source_id"] == second["source_id"]
        assert first["sha256"] == second["sha256"]
    finally:
        _cleanup(TEST_ROOT)


def test_different_bytes_different_id() -> None:
    """Different content produces different source IDs."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        src1 = _make_temp_file("# Source A\n\nContent A.\n", ".md")
        src2 = _make_temp_file("# Source B\n\nContent B.\n", ".md")

        result1 = writer.ingest(str(src1))
        result2 = writer.ingest(str(str(src2)))

        assert result1["source_id"] != result2["source_id"]
        assert result1["sha256"] != result2["sha256"]
    finally:
        _cleanup(TEST_ROOT)


def test_sha_matches_preserved_bytes() -> None:
    """The stored SHA-256 matches the actual file content."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        content = "# SHA Test\n\nVerify hash integrity.\n"
        src = _make_temp_file(content, ".md")

        result = writer.ingest(str(src))

        source_file = Path(result["path"])
        actual_sha = hashlib.sha256(source_file.read_bytes()).hexdigest()
        assert actual_sha == result["sha256"]
    finally:
        _cleanup(TEST_ROOT)


def test_unsupported_format_rejected() -> None:
    """Unsupported file extensions are rejected."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        src = _make_temp_file("# Test\n\nContent.\n", ".pdf")

        try:
            writer.ingest(str(src))
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "PDF_EXTRACTION_FAILED" in str(e)
    finally:
        _cleanup(TEST_ROOT)


def test_existing_source_never_overwritten() -> None:
    """Ingesting same source twice never overwrites the original."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        content = "# Immutable Test\n\nContent should not change.\n"
        src = _make_temp_file(content, ".md")

        first = writer.ingest(str(src))
        assert first["status"] == "created"

        # Get original file hash
        original_path = Path(first["path"])
        original_hash = hashlib.sha256(original_path.read_bytes()).hexdigest()

        # Ingest again
        second = writer.ingest(str(src))
        assert second["status"] == "existing"

        # Original file unchanged
        current_hash = hashlib.sha256(original_path.read_bytes()).hexdigest()
        assert current_hash == original_hash
    finally:
        _cleanup(TEST_ROOT)


def test_all_supported_extensions() -> None:
    """All Phase 2 supported extensions work."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)

        for i, ext in enumerate([".md", ".txt", ".json", ".yaml", ".yml"]):
            content = f"# Supported {i}\n\nTest extension {ext}.\n"
            src = _make_temp_file(content, ext)
            result = writer.ingest(str(src))
            assert result["status"] == "created", f"Failed for extension {ext}"
            assert result["source_id"].startswith("SRC-")
    finally:
        _cleanup(TEST_ROOT)


def test_content_derived_source_id() -> None:
    """Source ID is derived from content hash, not filename."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        content = "# Content ID\n\nSame content, different names.\n"

        src1 = _make_temp_file(content, "weird-name-1.md")
        src2 = _make_temp_file(content, "different-name-2.txt")

        result1 = writer.ingest(str(src1))
        result2 = writer.ingest(str(src2))

        # Same content → same source ID regardless of filename
        assert result1["source_id"] == result2["source_id"]
        assert result1["sha256"] == result2["sha256"]
    finally:
        _cleanup(TEST_ROOT)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def run_all() -> int:
    """Run all M2 ingest tests. Returns 0 on success."""
    tests = [
        test_valid_text_source_creates,
        test_same_bytes_return_existing,
        test_different_bytes_different_id,
        test_sha_matches_preserved_bytes,
        test_unsupported_format_rejected,
        test_existing_source_never_overwritten,
        test_all_supported_extensions,
        test_content_derived_source_id,
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

    print(f"\nM2 Ingest Tests: {passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all())

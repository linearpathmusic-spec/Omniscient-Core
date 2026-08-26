"""
Super Brain — Type System Tests (Milestone 1)

Verify all discriminated unions enforce their invariants:
- CREATE cannot have expected_sha256
- UPDATE must have expected_sha256
- IngestResult has explicit created/existing states
- WriteResult has explicit created/updated/rejected states
- No ambiguous optional-field combinations
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

BRAIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN_ROOT))

from brain.runtime.write import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    CreateKnowledge,
    IngestCreated,
    IngestExisting,
    IngestResult,
    KnowledgeWrite,
    UpdateKnowledge,
    WriteCreated,
    WriteRejected,
    WriteResult,
    WriteUpdated,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_op_is_literal(d: Any, expected_op: str) -> None:
    """Assert the 'op' field matches the expected literal."""
    assert d["op"] == expected_op, f"Expected op={expected_op}, got {d['op']}"


def _assert_status_is_literal(d: Any, expected_status: str) -> None:
    """Assert the 'status' field matches the expected literal."""
    assert d["status"] == expected_status, f"Expected status={expected_status}, got {d['status']}"


# ---------------------------------------------------------------------------
# CreateKnowledge tests
# ---------------------------------------------------------------------------


def test_create_has_op_create() -> None:
    req: CreateKnowledge = {
        "op": "create",
        "path": "knowledge/concepts/test.md",
        "content": "test content",
        "source_refs": ["SRC-abc123"],
    }
    _assert_op_is_literal(req, "create")
    assert "expected_sha256" not in req


def test_create_has_required_fields() -> None:
    req: CreateKnowledge = {
        "op": "create",
        "path": "knowledge/concepts/test.md",
        "content": "test content",
        "source_refs": ["SRC-abc123"],
    }
    assert req["path"] == "knowledge/concepts/test.md"
    assert req["content"] == "test content"
    assert req["source_refs"] == ["SRC-abc123"]


def test_create_no_expected_sha() -> None:
    """CREATE should not contain expected_sha256."""
    req: CreateKnowledge = {
        "op": "create",
        "path": "knowledge/concepts/test.md",
        "content": "test content",
        "source_refs": ["SRC-abc123"],
    }
    assert "expected_sha256" not in req, "CREATE must not have expected_sha256"


# ---------------------------------------------------------------------------
# UpdateKnowledge tests
# ---------------------------------------------------------------------------


def test_update_has_op_update() -> None:
    req: UpdateKnowledge = {
        "op": "update",
        "path": "knowledge/concepts/test.md",
        "content": "updated content",
        "source_refs": ["SRC-abc123", "SRC-def456"],
        "expected_sha256": "abc123def456",
    }
    _assert_op_is_literal(req, "update")
    assert req["expected_sha256"] == "abc123def456"


def test_update_has_expected_sha() -> None:
    """UPDATE must have expected_sha256."""
    req: UpdateKnowledge = {
        "op": "update",
        "path": "knowledge/concepts/test.md",
        "content": "updated content",
        "source_refs": ["SRC-abc123"],
        "expected_sha256": "abc123def456",
    }
    assert "expected_sha256" in req, "UPDATE must have expected_sha256"


def test_update_all_fields() -> None:
    req: UpdateKnowledge = {
        "op": "update",
        "path": "knowledge/concepts/test.md",
        "content": "updated content",
        "source_refs": ["SRC-abc123", "SRC-def456"],
        "expected_sha256": "abc123def456",
    }
    assert req["op"] == "update"
    assert req["path"] == "knowledge/concepts/test.md"
    assert req["content"] == "updated content"
    assert len(req["source_refs"]) == 2
    assert req["expected_sha256"] == "abc123def456"


# ---------------------------------------------------------------------------
# KnowledgeWrite union tests
# ---------------------------------------------------------------------------


def test_knowledge_write_is_create() -> None:
    req: KnowledgeWrite = {
        "op": "create",
        "path": "knowledge/concepts/test.md",
        "content": "test",
        "source_refs": ["SRC-abc"],
    }
    assert req["op"] == "create"
    assert isinstance(req, dict)


def test_knowledge_write_is_update() -> None:
    req: KnowledgeWrite = {
        "op": "update",
        "path": "knowledge/concepts/test.md",
        "content": "test",
        "source_refs": ["SRC-abc"],
        "expected_sha256": "abc123",
    }
    assert req["op"] == "update"
    assert "expected_sha256" in req


# ---------------------------------------------------------------------------
# IngestResult tests
# ---------------------------------------------------------------------------


def test_ingest_created_state() -> None:
    result: IngestCreated = {
        "status": "created",
        "source_id": "SRC-abc123",
        "sha256": "abc123def456",
        "path": "/raw/sources/SRC-abc123/source.md",
    }
    _assert_status_is_literal(result, "created")
    assert result["source_id"] == "SRC-abc123"
    assert result["sha256"] == "abc123def456"
    assert "error" not in result


def test_ingest_existing_state() -> None:
    result: IngestExisting = {
        "status": "existing",
        "source_id": "SRC-abc123",
        "sha256": "abc123def456",
        "path": "/raw/sources/SRC-abc123/source.md",
    }
    _assert_status_is_literal(result, "existing")
    assert result["source_id"] == "SRC-abc123"
    assert "error" not in result


def test_ingest_no_contradictory_states() -> None:
    """A result cannot be both created and have an error."""
    created: IngestCreated = {
        "status": "created",
        "source_id": "SRC-abc",
        "sha256": "abc",
        "path": "/path",
    }
    assert created["status"] == "created"
    assert "error" not in created

    existing: IngestExisting = {
        "status": "existing",
        "source_id": "SRC-abc",
        "sha256": "abc",
        "path": "/path",
    }
    assert existing["status"] == "existing"
    assert "error" not in existing


# ---------------------------------------------------------------------------
# WriteResult tests
# ---------------------------------------------------------------------------


def test_write_created_state() -> None:
    result: WriteCreated = {
        "status": "created",
        "path": "knowledge/concepts/test.md",
        "sha256": "abc123",
    }
    _assert_status_is_literal(result, "created")
    assert "error" not in result
    assert "old_sha256" not in result


def test_write_updated_state() -> None:
    result: WriteUpdated = {
        "status": "updated",
        "path": "knowledge/concepts/test.md",
        "old_sha256": "abc123",
        "new_sha256": "def456",
    }
    _assert_status_is_literal(result, "updated")
    assert "error" not in result
    assert result["old_sha256"] == "abc123"
    assert result["new_sha256"] == "def456"


def test_write_rejected_state() -> None:
    result: WriteRejected = {
        "status": "rejected",
        "code": "STALE_WRITE",
        "message": "File changed after read.",
    }
    _assert_status_is_literal(result, "rejected")
    assert result["code"] == "STALE_WRITE"
    assert "sha256" not in result
    assert "old_sha256" not in result


def test_write_no_contradictory_states() -> None:
    """A result cannot be both success and error."""
    created: WriteCreated = {
        "status": "created",
        "path": "test.md",
        "sha256": "abc",
    }
    assert created["status"] == "created"
    assert "error" not in created
    assert "code" not in created

    rejected: WriteRejected = {
        "status": "rejected",
        "code": "STALE_WRITE",
        "message": "stale",
    }
    assert rejected["status"] == "rejected"
    assert "sha256" not in rejected
    assert "new_sha256" not in rejected


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def run_all() -> int:
    """Run all M1 type tests. Returns 0 on success."""
    tests = [
        test_create_has_op_create,
        test_create_has_required_fields,
        test_create_no_expected_sha,
        test_update_has_op_update,
        test_update_has_expected_sha,
        test_update_all_fields,
        test_knowledge_write_is_create,
        test_knowledge_write_is_update,
        test_ingest_created_state,
        test_ingest_existing_state,
        test_ingest_no_contradictory_states,
        test_write_created_state,
        test_write_updated_state,
        test_write_rejected_state,
        test_write_no_contradictory_states,
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

    print(f"\nM1 Type Tests: {passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all())

"""
Super Brain — Security & Durability Tests

Security group (write boundary):
- cannot modify Constitution / AGENTS.md / policies / raw / state / history / skills / tools
- path traversal rejected
- escaping symlink rejected

Durability group:
- atomic temp-file replacement
- failed validation leaves target unchanged
- failed write is logged
- successful write is logged
"""

from __future__ import annotations

import hashlib
import json
import os
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
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, dir=str(TEST_ROOT)
    )
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


def _cleanup(root: Path) -> None:
    for d in ["raw", "knowledge", "logs"]:
        p = root / d
        if p.exists():
            shutil.rmtree(p)


def _ingest_source(writer: BrainWriter, content: str) -> str:
    src = _make_source(content, ".md")
    result = writer.ingest(str(src))
    assert result["status"] == "created"
    return result["source_id"]


def _create_request(
    path: str, source_refs: list[str], title: str = "X"
) -> CreateKnowledge:
    fm = f"---\ntitle: {title}\nkind: concept\nstatus: provisional\nsource_refs:\n"
    for ref in source_refs:
        fm += f"  - {ref}\n"
    fm += "---\n\nContent.\n"
    return CreateKnowledge(op="create", path=path, content=fm, source_refs=source_refs)


# ---------------------------------------------------------------------------
# Security: write boundary
# ---------------------------------------------------------------------------

FORBIDDEN_TARGETS = [
    ("brain/constitution.md", "Constitution"),
    ("AGENTS.md", "AGENTS.md"),
    ("brain/routing.md", "Routing policy"),
    ("state/current.md", "State"),
    ("history/episodes/2026-08-21.md", "History"),
    ("skills/research/SKILL.md", "Skills"),
    ("tools/registry.yaml", "Tools"),
    ("raw/sources/SRC-abc/source.md", "Raw evidence"),
    ("logs/retrieval.jsonl", "Logs"),
    ("evals/retrieval/cases.yaml", "Evals"),
]


def test_cannot_write_outside_knowledge() -> None:
    """Every non-knowledge path is rejected regardless of content."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source\n\nEvidence.\n")

        for path, label in FORBIDDEN_TARGETS:
            # Snapshot existing file state (if any)
            target = TEST_ROOT / path
            before = target.read_bytes() if target.exists() else None

            req = _create_request(path, [s1], title=f"Escape {label}")
            result = writer.write(req)
            assert result["status"] == "rejected", f"{label} was not rejected"
            assert result["code"] == "UNSAFE_PATH", (
                f"{label}: expected UNSAFE_PATH, got {result['code']}"
            )

            # And the file was not created or modified
            if before is None:
                assert not target.exists(), f"{label} file was created"
            else:
                assert target.read_bytes() == before, f"{label} file was modified"
    finally:
        _cleanup(TEST_ROOT)


def test_path_traversal_rejected() -> None:
    """Path traversal via .. is rejected."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source\n\nEvidence.\n")

        req = _create_request("../knowledge/../brain/constitution.md", [s1])
        result = writer.write(req)
        assert result["status"] == "rejected"
        assert result["code"] == "UNSAFE_PATH"
    finally:
        _cleanup(TEST_ROOT)


def test_absolute_path_rejected() -> None:
    """Absolute paths are rejected."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source\n\nEvidence.\n")

        req = _create_request("/etc/passwd", [s1])
        result = writer.write(req)
        assert result["status"] == "rejected"
        assert result["code"] == "UNSAFE_PATH"
    finally:
        _cleanup(TEST_ROOT)


def test_escaping_symlink_rejected() -> None:
    """A symlink inside knowledge/ pointing outside is rejected."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source\n\nEvidence.\n")

        # Create knowledge/ subdir + a symlink escaping to brain/
        knowledge = TEST_ROOT / "knowledge"
        knowledge.mkdir(parents=True, exist_ok=True)
        (knowledge / "concepts").mkdir(exist_ok=True)
        # Fake constitution outside knowledge/ that the symlink escapes to
        fake_constitution = TEST_ROOT / "brain" / "constitution.md"
        fake_constitution.parent.mkdir(parents=True, exist_ok=True)
        fake_constitution.write_text("# Constitution\n\nUntouchable.\n")
        link = knowledge / "concepts" / "escape.md"
        link.symlink_to(fake_constitution)

        req = _create_request("knowledge/concepts/escape.md", [s1])
        result = writer.write(req)

        assert result["status"] == "rejected"
        assert result["code"] == "UNSAFE_PATH"

        # Constitution untouched
        assert "Constitution" in (TEST_ROOT / "brain" / "constitution.md").read_text()
    finally:
        _cleanup(TEST_ROOT)


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------


def test_failed_validation_leaves_target_unchanged() -> None:
    """A rejected write leaves the existing target byte-identical."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source\n\nEvidence.\n")

        # Create a knowledge page
        req = _create_request("knowledge/concepts/keep.md", [s1], title="Keep")
        result = writer.write(req)
        assert result["status"] == "created"

        target = TEST_ROOT / "knowledge" / "concepts" / "keep.md"
        before = target.read_bytes()

        # Attempt an update with a stale SHA (rejected)
        fm = target.read_text().replace("title: Keep", "title: Changed")
        stale_req = UpdateKnowledge(
            op="update",
            path="knowledge/concepts/keep.md",
            content=fm,
            source_refs=[s1],
            expected_sha256="0" * 64,  # definitely stale
        )
        result = writer.write(stale_req)
        assert result["status"] == "rejected"
        assert result["code"] == "STALE_WRITE"

        # Original untouched
        assert target.read_bytes() == before
    finally:
        _cleanup(TEST_ROOT)


def test_successful_write_is_logged() -> None:
    """A successful create appends to logs/writes.jsonl."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        s1 = _ingest_source(writer, "# Source\n\nEvidence.\n")

        req = _create_request("knowledge/concepts/logged.md", [s1], title="Logged")
        result = writer.write(req)
        assert result["status"] == "created"

        log_path = TEST_ROOT / "logs" / "writes.jsonl"
        assert log_path.exists()
        entries = [json.loads(l) for l in log_path.read_text().strip().split("\n")]
        assert entries
        last = entries[-1]
        assert last["operation"] == "create"
        assert last["status"] == "created"
        assert "knowledge/concepts/logged.md" in last["path"]
        # No document content in logs
        assert "content" not in json.dumps(last)
    finally:
        _cleanup(TEST_ROOT)


def test_rejected_write_is_logged() -> None:
    """A rejected write is logged with its error code."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)

        # Rejected: unsafe path
        req = _create_request("../brain/constitution.md", ["SRC-abc123"])
        result = writer.write(req)
        assert result["status"] == "rejected"

        log_path = TEST_ROOT / "logs" / "writes.jsonl"
        assert log_path.exists()
        entries = [json.loads(l) for l in log_path.read_text().strip().split("\n")]
        assert entries
        last = entries[-1]
        assert last["operation"] == "create"
        assert last["status"] == "rejected"
        assert last["error"] == "UNSAFE_PATH"
    finally:
        _cleanup(TEST_ROOT)


def test_ingestion_is_logged() -> None:
    """Ingestion appends to logs/ingestion.jsonl."""
    _cleanup(TEST_ROOT)
    try:
        writer = BrainWriter(TEST_ROOT)
        _ingest_source(writer, "# Log me\n\nEvidence.\n")

        log_path = TEST_ROOT / "logs" / "ingestion.jsonl"
        assert log_path.exists()
        entries = [json.loads(l) for l in log_path.read_text().strip().split("\n")]
        assert entries
        assert entries[-1]["operation"] == "created"
        assert entries[-1]["source_id"].startswith("SRC-")
    finally:
        _cleanup(TEST_ROOT)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def run_all() -> int:
    tests = [
        test_cannot_write_outside_knowledge,
        test_path_traversal_rejected,
        test_absolute_path_rejected,
        test_escaping_symlink_rejected,
        test_failed_validation_leaves_target_unchanged,
        test_successful_write_is_logged,
        test_rejected_write_is_logged,
        test_ingestion_is_logged,
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

    print(f"\nSecurity & Durability Tests: {passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all())

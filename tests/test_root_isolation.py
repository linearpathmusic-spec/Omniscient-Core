"""
Super Brain — Root Isolation Regression Test

Invariant under test:

    Brain(root=X) — EVERY runtime read/write/log must remain beneath X.

Phase 2 hit this bug class once (temp files polluting repo root), Phase 3
again (_log_retrieval writing to the global BRAIN_ROOT/logs even when the
search instance was rooted elsewhere). This test locks the invariant:

  - knowledge writes land in the instance root, not a sibling root
  - ingested sources land in the instance root
  - all three logs (writes / ingestion / retrieval) land in the instance
    root's logs/, never the real BRAIN_ROOT's
  - two instances rooted at A and B never cross-contaminate

Usage:
    python tests/test_root_isolation.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

BRAIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN_ROOT))

TEST_ROOT = Path(tempfile.mkdtemp(prefix="sb-isolation-"))

from brain.runtime.write import BrainWriter, CreateKnowledge  # noqa: E402  # pyright: ignore[reportMissingImports]
from brain.runtime.search import BrainSearch  # noqa: E402  # pyright: ignore[reportMissingImports]


def _cleanup(root: Path) -> None:
    for d in ["raw", "knowledge", "logs", "brain"]:
        p = root / d
        if p.exists():
            shutil.rmtree(p)


def _reset_brains(root_a: Path, root_b: Path) -> None:
    """Wipe both brain roots so each test starts clean."""
    for root in [root_a, root_b]:
        _cleanup(root)
        for sub in ["knowledge/concepts", "raw/sources", "logs"]:
            (root / sub).mkdir(parents=True, exist_ok=True)


def _make_brain(root: Path) -> tuple[BrainWriter, BrainSearch]:
    """Scaffold a minimal brain tree and return (writer, search)."""
    for sub in ["knowledge/concepts", "raw/sources", "logs"]:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return BrainWriter(root), BrainSearch(root)


def _seed_knowledge(writer: BrainWriter, root: Path, title: str) -> str:
    """Write one knowledge doc with a freshly ingested source; return source id."""
    src = Path(tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, dir=str(TEST_ROOT)
    ).name)
    src.write_text(f"# {title}\n\nEvidence for {title}.\n", encoding="utf-8")

    ingest = writer.ingest(str(src))
    ref = ingest["source_id"]

    content = (
        f"---\ntitle: {title}\nkind: concept\n"
        f"source_refs:\n  - {ref}\nstatus: provisional\n---\n\n"
        f"# {title}\n\nEvidence for {title}.\n"
    )
    result = writer.write(CreateKnowledge(
        op="create",
        path=f"knowledge/concepts/{title.lower()}.md",
        content=content,
        source_refs=[ref],
    ))
    assert result["status"] == "created"
    return ref


def _log_lines(log_file: Path) -> list[str]:
    if not log_file.exists():
        return []
    return [l for l in log_file.read_text(encoding="utf-8").strip().split("\n") if l]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_write_stays_in_instance_root() -> None:
    """A knowledge write lands under root A, not a sibling root B."""
    root_a = TEST_ROOT / "brain_a"
    root_b = TEST_ROOT / "brain_b"
    _reset_brains(root_a, root_b)
    writer_a, _ = _make_brain(root_a)
    writer_b, _ = _make_brain(root_b)

    try:
        _seed_knowledge(writer_a, root_a, "Isolation")

        assert (root_a / "knowledge/concepts/isolation.md").exists()
        assert not (root_b / "knowledge/concepts/isolation.md").exists()
        # Real brain untouched
        assert not (BRAIN_ROOT / "knowledge/concepts/isolation.md").exists()

        # B's search cannot see A's knowledge
        search_b = BrainSearch(root_b)
        resp = search_b.query("Isolation", top_k=5)
        assert resp.result_count == 0

        # B can still write its own independent knowledge
        _seed_knowledge(writer_b, root_b, "Independent")
        assert (root_b / "knowledge/concepts/independent.md").exists()
        assert not (root_a / "knowledge/concepts/independent.md").exists()
    finally:
        _cleanup(root_a)
        _cleanup(root_b)


def test_ingest_stays_in_instance_root() -> None:
    """Ingested sources land under root A, not root B or BRAIN_ROOT."""
    root_a = TEST_ROOT / "brain_a"
    root_b = TEST_ROOT / "brain_b"
    _reset_brains(root_a, root_b)
    writer_a, _ = _make_brain(root_a)
    writer_b, _ = _make_brain(root_b)

    try:
        ref = _seed_knowledge(writer_a, root_a, "SourceHome")

        # Source preserved under A only
        assert (root_a / "raw" / "sources" / ref).exists()
        assert not (root_b / "raw" / "sources" / ref).exists()
        assert not (BRAIN_ROOT / "raw" / "sources" / ref).exists()

        # A's source integrity check passes only against A's writer
        writer_a._verify_source_integrity(ref)  # no exception
    finally:
        _cleanup(root_a)
        _cleanup(root_b)


def test_writes_and_ingestion_logs_go_to_instance_root() -> None:
    """writes.jsonl and ingestion.jsonl are written under the instance root."""
    root_a = TEST_ROOT / "brain_a"
    root_b = TEST_ROOT / "brain_b"
    _reset_brains(root_a, root_b)
    writer_a, _ = _make_brain(root_a)
    writer_b, _ = _make_brain(root_b)

    try:
        # Snapshot real-brain logs before any activity
        real_writes = _log_lines(BRAIN_ROOT / "logs" / "writes.jsonl")
        real_ingest = _log_lines(BRAIN_ROOT / "logs" / "ingestion.jsonl")

        _seed_knowledge(writer_a, root_a, "LogA")
        _seed_knowledge(writer_b, root_b, "LogB")

        a_writes = _log_lines(root_a / "logs" / "writes.jsonl")
        a_ingest = _log_lines(root_a / "logs" / "ingestion.jsonl")
        b_writes = _log_lines(root_b / "logs" / "writes.jsonl")

        assert len(a_writes) == 1
        assert any("loga.md" in json.loads(l)["path"] for l in a_writes)
        assert len(a_ingest) == 1
        assert len(b_writes) == 1
        assert any("logb.md" in json.loads(l)["path"] for l in b_writes)

        # Real brain's logs untouched
        assert _log_lines(BRAIN_ROOT / "logs" / "writes.jsonl") == real_writes
        assert _log_lines(BRAIN_ROOT / "logs" / "ingestion.jsonl") == real_ingest
    finally:
        _cleanup(root_a)
        _cleanup(root_b)


def test_retrieval_log_goes_to_instance_root() -> None:
    """brain_query logs to the instance root's retrieval.jsonl (regression)."""
    root_a = TEST_ROOT / "brain_a"
    root_b = TEST_ROOT / "brain_b"
    _reset_brains(root_a, root_b)
    writer_a, search_a = _make_brain(root_a)
    _, search_b = _make_brain(root_b)

    try:
        real_retrieval = _log_lines(BRAIN_ROOT / "logs" / "retrieval.jsonl")

        _seed_knowledge(writer_a, root_a, "RetrievalHome")
        search_a.query("RetrievalHome", top_k=3)
        search_b.query("RetrievalHome", top_k=3)  # no hits in B, still logged

        a_logs = _log_lines(root_a / "logs" / "retrieval.jsonl")
        b_logs = _log_lines(root_b / "logs" / "retrieval.jsonl")

        # Each instance logged to its own root exactly once
        assert len(a_logs) == 1
        assert len(b_logs) == 1
        assert json.loads(a_logs[0])["query"] == "RetrievalHome"
        assert json.loads(b_logs[0])["query"] == "RetrievalHome"

        # Regression: the real brain's retrieval log is NOT appended to
        assert _log_lines(BRAIN_ROOT / "logs" / "retrieval.jsonl") == real_retrieval
    finally:
        _cleanup(root_a)
        _cleanup(root_b)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def run_all() -> int:
    tests = [
        test_write_stays_in_instance_root,
        test_ingest_stays_in_instance_root,
        test_writes_and_ingestion_logs_go_to_instance_root,
        test_retrieval_log_goes_to_instance_root,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
            print(f"PASS: {test.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {test.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {test.__name__}: {e}")

    print(f"\nRoot Isolation Tests: {passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all())

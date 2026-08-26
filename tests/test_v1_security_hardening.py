"""
Super Brain — v1 Security & Concurrency Hardening Tests

Tests for the v1 hardening fixes:
- Fix #2: brain_query exposes epistemic status
- Fix #3: verification evidence must be subset of page source_refs
- Fix #4: source integrity triple-check (actual SHA, metadata SHA, SRC-* prefix)
- Fix #5: authority preservation on re-ingest
- Fix #6: backup/restore with staging and validation
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import shutil
import tempfile
from pathlib import Path

import pytest

# Ensure super-brain root is on the path
BRAIN_ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(BRAIN_ROOT))

from brain.runtime.write import BrainWriter, CreateKnowledge  # noqa: E402
from brain.runtime.review import BrainReviewer, VerifyKnowledge  # noqa: E402
from brain.runtime.search import BrainSearch  # noqa: E402


def _concurrent_update_worker(root, barrier, queue, path, sha, source_id, marker):
    writer = BrainWriter(root=Path(root))
    content = (
        "---\ntitle: Race\nkind: concept\nstatus: provisional\nsource_refs:\n"
        f"  - {source_id}\n---\n\n{marker}\n"
    )
    barrier.wait()
    queue.put(writer.write({
        "op": "update", "path": path, "content": content,
        "source_refs": [source_id], "expected_sha256": sha,
    }))


def _concurrent_create_worker(root, barrier, queue, path, source_id, marker):
    writer = BrainWriter(root=Path(root))
    content = (
        "---\ntitle: Concurrent Create\nkind: concept\nstatus: provisional\nsource_refs:\n"
        f"  - {source_id}\n---\n\n{marker}\n"
    )
    barrier.wait()
    queue.put(writer.write({
        "op": "create", "path": path, "content": content,
        "source_refs": [source_id],
    }))


def _concurrent_review_worker(root, barrier, queue, path, sha, source_id):
    reviewer = BrainReviewer(root=Path(root))
    barrier.wait()
    queue.put(reviewer.review({
        "decision": "verify", "path": path, "expected_sha256": sha,
        "evidence_refs": [source_id], "rationale": "Evidence supports.",
    }))


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


def tmp_source(writer: BrainWriter, content: str, suffix: str = ".md") -> Path:
    """Write a temp source file (not ingested)."""
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
        op="create",
        path=path,
        content=fm,
        source_refs=source_refs,
    )
    result = writer.write(req)
    assert result["status"] == "created", result
    return result["sha256"]


# ---------------------------------------------------------------------------
# Fix #1: real cross-process mutation serialization
# ---------------------------------------------------------------------------


def test_concurrent_updates_have_exactly_one_winner(temp_root, writer) -> None:
    src = tmp_source(writer, "# Evidence\n\nClaim.\n")
    source_id = writer.ingest(str(src))["source_id"]
    sha = _create_knowledge(writer, "knowledge/concepts/race.md", "Race", [source_id])

    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    processes = [
        ctx.Process(target=_concurrent_update_worker, args=(
            str(temp_root), barrier, queue, "knowledge/concepts/race.md",
            sha, source_id, marker,
        ))
        for marker in ("writer-one", "writer-two")
    ]
    for process in processes:
        process.start()
    results = [queue.get(timeout=5) for _ in processes]
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    assert sorted(result["status"] for result in results) == ["rejected", "updated"]
    rejection = next(result for result in results if result["status"] == "rejected")
    assert rejection["code"] == "STALE_WRITE"


def test_concurrent_creates_do_not_overwrite(temp_root, writer) -> None:
    src = tmp_source(writer, "# Evidence\n\nClaim.\n")
    source_id = writer.ingest(str(src))["source_id"]
    path = "knowledge/concepts/concurrent.md"

    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    processes = [
        ctx.Process(target=_concurrent_create_worker, args=(
            str(temp_root), barrier, queue, path, source_id, marker,
        ))
        for marker in ("creator-one", "creator-two")
    ]
    for process in processes:
        process.start()
    results = [queue.get(timeout=5) for _ in processes]
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    assert sorted(result["status"] for result in results) == ["created", "rejected"]
    rejection = next(result for result in results if result["status"] == "rejected")
    assert rejection["code"] == "TARGET_EXISTS"


def test_review_and_update_share_one_transaction_lock(temp_root, writer) -> None:
    src = tmp_source(writer, "# Evidence\n\nClaim.\n")
    source_id = writer.ingest(str(src))["source_id"]
    path = "knowledge/concepts/race.md"
    sha = _create_knowledge(writer, path, "Race", [source_id])

    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    update = ctx.Process(target=_concurrent_update_worker, args=(
        str(temp_root), barrier, queue, path, sha, source_id, "updated",
    ))
    review = ctx.Process(target=_concurrent_review_worker, args=(
        str(temp_root), barrier, queue, path, sha, source_id,
    ))
    update.start()
    review.start()
    results = [queue.get(timeout=5), queue.get(timeout=5)]
    update.join(timeout=5)
    review.join(timeout=5)
    assert update.exitcode == review.exitcode == 0
    assert sum(result["status"] in {"updated", "verified"} for result in results) == 1
    rejected = next(result for result in results if result["status"] == "rejected")
    assert rejected["code"] in {"STALE_WRITE", "STALE_REVIEW"}


# ---------------------------------------------------------------------------
# Fix #2: brain_query exposes epistemic status
# ---------------------------------------------------------------------------


def test_query_exposes_epistemic_status(temp_root, writer, reviewer) -> None:
    """Public brain_query API must expose status field (Phase 6 exit criterion)."""
    src = tmp_source(writer, "# Evidence\n\nPrimary claim.\n")
    source_id = writer.ingest(str(src))["source_id"]

    sha = _create_knowledge(writer, "knowledge/concepts/rag.md", "RAG", [source_id])

    # Verify the knowledge
    from brain.runtime.review import VerifyKnowledge
    verify_req = VerifyKnowledge(
        decision="verify",
        path="knowledge/concepts/rag.md",
        expected_sha256=sha,
        evidence_refs=[source_id],
        rationale="Evidence supports the claims.",
    )
    reviewer.review(verify_req)

    # Query and check status is exposed
    engine = BrainSearch(root=temp_root)
    response = engine.query("RAG retrieval", top_k=5)

    assert response.results
    hit = next(r for r in response.results if "rag" in r.path)
    assert hasattr(hit, "status")
    assert hit.status == "verified"


# ---------------------------------------------------------------------------
# Fix #3: verification evidence must be subset of page source_refs
# ---------------------------------------------------------------------------


def test_verify_rejects_external_evidence(temp_root, writer, reviewer) -> None:
    """Verification cannot use evidence not cited by the page."""
    src1 = tmp_source(writer, "# Source A\n\nClaim A.\n")
    src2 = tmp_source(writer, "# Source B\n\nClaim B.\n")
    s1 = writer.ingest(str(src1))["source_id"]
    s2 = writer.ingest(str(src2))["source_id"]

    # Create with only s1
    sha = _create_knowledge(writer, "knowledge/concepts/test.md", "Test", [s1])

    # Try to verify with s2 (not in page's source_refs)
    from brain.runtime.review import VerifyKnowledge
    verify_req = VerifyKnowledge(
        decision="verify",
        path="knowledge/concepts/test.md",
        expected_sha256=sha,
        evidence_refs=[s2],  # s2 is NOT in page's source_refs
        rationale="Evidence supports.",
    )
    result = reviewer.review(verify_req)

    assert result["status"] == "rejected"
    assert result["code"] == "INVALID_EVIDENCE"
    assert "subset" in result["message"].lower() or "extra" in result["message"].lower()


def test_verify_accepts_subset_evidence(temp_root, writer, reviewer) -> None:
    """Verification accepts evidence that is a subset of page source_refs."""
    src1 = tmp_source(writer, "# Source A\n\nClaim A.\n")
    src2 = tmp_source(writer, "# Source B\n\nClaim B.\n")
    s1 = writer.ingest(str(src1))["source_id"]
    s2 = writer.ingest(str(src2))["source_id"]

    # Create with both s1 and s2
    sha = _create_knowledge(writer, "knowledge/concepts/test.md", "Test", [s1, s2])

    # Verify with only s1 (subset of [s1, s2])
    from brain.runtime.review import VerifyKnowledge
    verify_req = VerifyKnowledge(
        decision="verify",
        path="knowledge/concepts/test.md",
        expected_sha256=sha,
        evidence_refs=[s1],  # subset of [s1, s2]
        rationale="Evidence supports.",
    )
    result = reviewer.review(verify_req)

    assert result["status"] == "verified"


# ---------------------------------------------------------------------------
# Fix #4: source integrity triple-check
# ---------------------------------------------------------------------------


def test_source_integrity_triple_check(temp_root, writer) -> None:
    """Source integrity checks all three: actual SHA, metadata SHA, SRC-* prefix."""
    src = tmp_source(writer, "# Source\n\nContent.\n")
    result = writer.ingest(str(src))
    source_id = result["source_id"]

    # Valid source passes
    writer._verify_source_integrity(source_id)

    # Tamper with source.md only -> actual SHA changes, metadata SHA doesn't
    source_md = temp_root / "raw" / "sources" / source_id / "source.md"
    source_md.write_text(source_md.read_text() + "\n# Tampered\n")

    with pytest.raises(ValueError, match="SOURCE_INTEGRITY_FAILED"):
        writer._verify_source_integrity(source_id)

    # Restore source, tamper with metadata.yaml only
    source_md.write_text("# Source\n\nContent.\n")
    meta_path = temp_root / "raw" / "sources" / source_id / "metadata.yaml"
    meta_text = meta_path.read_text()
    # Change the SHA in metadata
    import re
    meta_text = re.sub(r"sha256: .+", "sha256: deadbeef", meta_text)
    meta_path.write_text(meta_text)

    with pytest.raises(ValueError, match="SOURCE_INTEGRITY_FAILED"):
        writer._verify_source_integrity(source_id)


def test_source_id_prefix_mismatch_detected(temp_root, writer) -> None:
    """If source_id prefix doesn't match actual content hash, it's rejected."""
    src = tmp_source(writer, "# Source\n\nContent.\n")
    result = writer.ingest(str(src))
    source_id = result["source_id"]

    # Verify passes initially
    writer._verify_source_integrity(source_id)

    # Tamper with source.md to change its hash
    source_md = temp_root / "raw" / "sources" / source_id / "source.md"
    original_content = source_md.read_text()
    source_md.write_text(original_content + "\n# Added\n")

    # Now the source_id prefix won't match the actual content hash
    with pytest.raises(ValueError, match="SOURCE_INTEGRITY_FAILED"):
        writer._verify_source_integrity(source_id)


def test_missing_source_payload_is_rejected(temp_root, writer) -> None:
    src = tmp_source(writer, "# Source\n\nContent.\n")
    result = writer.ingest(str(src))
    source_id = result["source_id"]
    (temp_root / "raw" / "sources" / source_id / "source.md").unlink()

    with pytest.raises(ValueError, match="source.md missing"):
        writer._verify_source_integrity(source_id)


# ---------------------------------------------------------------------------
# Fix #5: authority preservation on re-ingest
# ---------------------------------------------------------------------------


def test_re_ingest_preserves_authority(temp_root, writer) -> None:
    """Re-ingesting without specifying authority preserves existing classification."""
    src = tmp_source(writer, "# Official Standard\n\nNormative text.\n")

    # First ingest with primary authority
    r1 = writer.ingest(str(src), authority="primary")
    assert r1["status"] == "created"

    meta_path = temp_root / "raw" / "sources" / r1["source_id"] / "metadata.yaml"
    meta_text = meta_path.read_text()
    assert "authority: primary" in meta_text

    # Re-ingest without specifying authority (should preserve "primary")
    r2 = writer.ingest(str(src))
    assert r2["status"] == "existing"

    meta_text = meta_path.read_text()
    assert "authority: primary" in meta_text


def test_re_ingest_with_explicit_authority_updates(temp_root, writer) -> None:
    """Re-ingesting with explicit authority updates the classification."""
    src = tmp_source(writer, "# Forum Post\n\nOpinion.\n")

    # First ingest with unknown authority
    r1 = writer.ingest(str(src), authority="unknown")
    assert r1["status"] == "created"

    meta_path = temp_root / "raw" / "sources" / r1["source_id"] / "metadata.yaml"
    meta_text = meta_path.read_text()
    assert "authority: unknown" in meta_text

    # Re-ingest with explicit primary authority
    r2 = writer.ingest(str(src), authority="primary")
    assert r2["status"] == "existing"

    meta_text = meta_path.read_text()
    assert "authority: primary" in meta_text


def test_public_ingest_omission_preserves_authority(temp_root, monkeypatch) -> None:
    import tools.brain_ingest as ingest_tool

    monkeypatch.setattr(ingest_tool, "BRAIN_ROOT", temp_root)
    src = temp_root / "official.md"
    src.write_text("# Official\n")
    first = ingest_tool.brain_ingest(str(src), authority="primary")
    ingest_tool.brain_ingest(str(src))
    metadata = temp_root / "raw" / "sources" / first["source_id"] / "metadata.yaml"
    assert "authority: primary" in metadata.read_text()


def test_new_source_defaults_to_unknown(temp_root, writer) -> None:
    """New sources default to 'unknown' authority when not specified."""
    src = tmp_source(writer, "# Source\n\nContent.\n")

    # Ingest without specifying authority
    r = writer.ingest(str(src))
    assert r["status"] == "created"

    meta_path = temp_root / "raw" / "sources" / r["source_id"] / "metadata.yaml"
    meta_text = meta_path.read_text()
    assert "authority: unknown" in meta_text


# ---------------------------------------------------------------------------
# Fix #6: backup/restore with staging and validation
# ---------------------------------------------------------------------------


def test_backup_creates_manifest(temp_root) -> None:
    """Backup embeds a manifest so archive and hashes travel together."""
    from scripts.brain_backup import create_backup, _set_root

    # Set root to temp_root for testing
    _set_root(temp_root)

    # Create some test files
    (temp_root / "knowledge").mkdir(parents=True)
    (temp_root / "knowledge" / "test.md").write_text("test content")

    archive = create_backup()
    import zipfile
    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read("backup-manifest.json"))
    assert "files" in manifest
    assert "created_at" in manifest
    assert len(manifest["files"]) > 0


def test_restore_uses_staging_directory(temp_root) -> None:
    """Restore extracts to staging directory before promoting."""
    from scripts.brain_backup import create_backup, restore_backup, _set_root

    # Set root to temp_root for testing
    _set_root(temp_root)

    # Create test files
    (temp_root / "knowledge").mkdir(parents=True)
    (temp_root / "knowledge" / "test.md").write_text(
        "---\ntitle: Test\nkind: concept\nstatus: provisional\n"
        "source_refs: []\n---\n\noriginal content"
    )

    # Create backup
    archive = create_backup()

    # Modify the file
    (temp_root / "knowledge" / "test.md").write_text("modified content")

    # Restore
    restore_backup(archive)

    # Verify restoration
    content = (temp_root / "knowledge" / "test.md").read_text()
    assert content.endswith("original content")


def test_restore_validates_archive(temp_root) -> None:
    """Restore validates archive before extracting."""
    from scripts.brain_backup import restore_backup, _set_root

    # Set root to temp_root for testing
    _set_root(temp_root)

    # Create an invalid zip
    invalid_zip = temp_root / "invalid.zip"
    invalid_zip.write_bytes(b"not a zip file")

    with pytest.raises(ValueError, match="Invalid zip archive"):
        restore_backup(invalid_zip)


def test_restore_rejects_path_traversal(temp_root) -> None:
    """Restore rejects archives with path traversal attempts."""
    import zipfile
    from scripts.brain_backup import restore_backup as _restore_backup, _set_root

    # Set root to temp_root for testing
    _set_root(temp_root)

    # Create an archive with path traversal
    malicious_zip = temp_root / "malicious.zip"
    with zipfile.ZipFile(malicious_zip, "w") as zf:
        zf.writestr("../../etc/passwd", "malicious content")

    with pytest.raises(ValueError, match="Path traversal detected"):
        _restore_backup(malicious_zip)


def test_restore_rejects_nested_path_traversal(temp_root) -> None:
    import zipfile
    from scripts.brain_backup import restore_backup, _set_root

    _set_root(temp_root)
    malicious_zip = temp_root / "nested-traversal.zip"
    with zipfile.ZipFile(malicious_zip, "w") as zf:
        zf.writestr("knowledge/../../outside", "malicious")
    with pytest.raises(ValueError, match="Path traversal detected"):
        restore_backup(malicious_zip)


def test_restore_rejects_payload_hash_mismatch(temp_root) -> None:
    import zipfile
    from scripts.brain_backup import create_backup, restore_backup, _set_root

    _set_root(temp_root)
    (temp_root / "knowledge").mkdir()
    (temp_root / "knowledge" / "test.md").write_text(
        "---\ntitle: Test\nkind: concept\nstatus: provisional\n"
        "source_refs: []\n---\n\ncontent"
    )
    original = create_backup()
    tampered = temp_root / "tampered.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as dest:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "knowledge/test.md":
                data += b"tampered"
            dest.writestr(info, data)
    with pytest.raises(ValueError, match="integrity check failed"):
        restore_backup(tampered)


def test_restore_rolls_back_when_promotion_fails(temp_root, monkeypatch) -> None:
    import scripts.brain_backup as backup

    backup._set_root(temp_root)
    (temp_root / "knowledge").mkdir()
    page = temp_root / "knowledge" / "test.md"
    page.write_text(
        "---\ntitle: Test\nkind: concept\nstatus: provisional\n"
        "source_refs: []\n---\n\nbacked-up"
    )
    archive = backup.create_backup()
    page.write_text(page.read_text().replace("backed-up", "live-current"))

    real_replace = backup.os.replace
    failed = False

    def fail_first_promotion(source, destination):
        nonlocal failed
        if not failed and ".restore-stage-" in str(source):
            failed = True
            raise OSError("injected promotion failure")
        return real_replace(source, destination)

    monkeypatch.setattr(backup.os, "replace", fail_first_promotion)
    with pytest.raises(OSError, match="injected promotion failure"):
        backup.restore_backup(archive)
    assert "live-current" in page.read_text()


def test_backup_includes_root_config_files(temp_root) -> None:
    """Backup includes AGENTS.md and other root config files."""
    from scripts.brain_backup import create_backup, _set_root

    # Set root to temp_root for testing
    _set_root(temp_root)

    # Create AGENTS.md
    (temp_root / "AGENTS.md").write_text("# AGENTS.md\n\nContent.")

    archive = create_backup()

    # Check that AGENTS.md is in the archive
    import zipfile
    with zipfile.ZipFile(archive, "r") as zf:
        names = zf.namelist()
        assert "AGENTS.md" in names


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])

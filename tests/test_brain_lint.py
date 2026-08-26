"""
Super Brain — Phase 8 Lint Tests

Tests for BrainLint deep module:
- Broken source refs (missing / malformed)
- Source integrity (tampered raw bytes)
- Status violations (invalid status, verified without evidence)
- Duplicate titles
- Stale wikilinks
- Skill schema drift
- Orphan sources / untouched knowledge (info level)
- Read-only guarantee (lint never modifies the corpus)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.runtime.lint import BrainLint, Finding


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path):
    """A temp brain root with a clean minimal corpus."""
    r = tmp_path
    for d in ["knowledge/concepts", "raw/sources", "skills/debugging",
              "history/decisions", "logs"]:
        (r / d).mkdir(parents=True, exist_ok=True)
    # One valid source
    src_dir = r / "raw" / "sources" / "SRC-aaaaaaaaaaaa"
    src_dir.mkdir()
    src_dir.joinpath("source.md").write_text("# Evidence\n\nClaim.\n")
    src_dir.joinpath("metadata.yaml").write_text(
        "source_id: SRC-aaaaaaaaaaaa\nsha256: "
        + _sha(src_dir / "source.md") + "\n"
    )
    # One valid knowledge page
    (r / "knowledge" / "concepts" / "valid.md").write_text(
        "---\ntitle: Valid Concept\nkind: concept\nstatus: provisional\n"
        "source_refs:\n  - SRC-aaaaaaaaaaaa\n---\n\nContent.\n"
    )
    return r


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lint(root: Path) -> list[Finding]:
    return BrainLint(root=root).run()


def _codes(findings: list[Finding]) -> set[str]:
    return {f.code for f in findings}


# ---------------------------------------------------------------------------
# Clean corpus -> no errors
# ---------------------------------------------------------------------------


def test_clean_corpus_no_errors(root):
    findings = _lint(root)
    assert not [f for f in findings if f.severity == "error"], findings


def test_lint_is_read_only(root):
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    _lint(root)
    after = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after


# ---------------------------------------------------------------------------
# Broken source refs
# ---------------------------------------------------------------------------


def test_missing_source_ref_reported(root):
    (root / "knowledge" / "concepts" / "broken.md").write_text(
        "---\ntitle: Broken\ndescription: x\nkind: concept\nstatus: provisional\n"
        "source_refs:\n  - SRC-deadbeef0000\n---\n\nContent.\n"
    )
    findings = _lint(root)
    assert "BROKEN_SOURCE_REF" in _codes(findings)
    assert any("SRC-deadbeef0000" in f.message for f in findings)


def test_malformed_source_ref_reported(root):
    (root / "knowledge" / "concepts" / "badref.md").write_text(
        "---\ntitle: Bad Ref\nkind: concept\nstatus: provisional\n"
        "source_refs:\n  - LES-test-001\n---\n\nContent.\n"
    )
    findings = _lint(root)
    assert any(f.code == "BROKEN_SOURCE_REF" and "LES-test-001" in f.message
               for f in findings)


# ---------------------------------------------------------------------------
# Source integrity
# ---------------------------------------------------------------------------


def test_tampered_source_reported(root):
    src = root / "raw" / "sources" / "SRC-aaaaaaaaaaaa" / "source.md"
    src.write_text(src.read_text() + "\n# Tampered\n")
    findings = _lint(root)
    assert "SOURCE_INTEGRITY" in _codes(findings)


def test_missing_metadata_hash_reported(root):
    meta = root / "raw" / "sources" / "SRC-aaaaaaaaaaaa" / "metadata.yaml"
    meta.write_text("source_id: SRC-aaaaaaaaaaaa\nsha256: \n")
    findings = _lint(root)
    assert "SOURCE_INTEGRITY" in _codes(findings)


# ---------------------------------------------------------------------------
# Status violations
# ---------------------------------------------------------------------------


def test_invalid_status_reported(root):
    (root / "knowledge" / "concepts" / "badstatus.md").write_text(
        "---\ntitle: Bad Status\nkind: concept\nstatus: accepted\n"
        "source_refs:\n  - SRC-aaaaaaaaaaaa\n---\n\nContent.\n"
    )
    findings = _lint(root)
    assert "INVALID_STATUS" in _codes(findings)


def test_verified_without_evidence_reported(root):
    (root / "knowledge" / "concepts" / "ghost.md").write_text(
        "---\ntitle: Ghost Verified\nkind: concept\nstatus: verified\n"
        "source_refs: []\n---\n\nContent.\n"
    )
    findings = _lint(root)
    assert "VERIFIED_WITHOUT_EVIDENCE" in _codes(findings)


# ---------------------------------------------------------------------------
# Duplicate titles
# ---------------------------------------------------------------------------


def test_duplicate_title_reported(root):
    (root / "knowledge" / "concepts" / "dupe.md").write_text(
        "---\ntitle: Valid Concept\nkind: concept\nstatus: provisional\n"
        "source_refs:\n  - SRC-aaaaaaaaaaaa\n---\n\nDupe.\n"
    )
    findings = _lint(root)
    assert "DUPLICATE_TITLE" in _codes(findings)


# ---------------------------------------------------------------------------
# Stale wikilinks
# ---------------------------------------------------------------------------


def test_stale_wikilink_reported(root):
    (root / "knowledge" / "concepts" / "links.md").write_text(
        "---\ntitle: Links\nkind: concept\nstatus: provisional\n"
        "source_refs:\n  - SRC-aaaaaaaaaaaa\n---\n\nSee [[nonexistent-page]] and [[concepts/valid]].\n"
    )
    (root / "knowledge" / "concepts" / "valid.md").write_text(
        "---\ntitle: Valid\nkind: concept\nstatus: provisional\n"
        "source_refs:\n  - SRC-aaaaaaaaaaaa\n---\n\nContent.\n"
    )
    findings = _lint(root)
    stale = [f for f in findings if f.code == "STALE_WIKILINK"]
    assert len(stale) == 1
    assert "nonexistent-page" in stale[0].message


# ---------------------------------------------------------------------------
# Skill schema
# ---------------------------------------------------------------------------


def test_invalid_skill_schema_reported(root):
    (root / "skills" / "debugging" / "SKILL.md").write_text(
        "---\nname: debugging\nscope: unknown-scope\nstatus: active\nversion: x\n---\n\nProcedure.\n"
    )
    findings = _lint(root)
    assert "INVALID_SKILL_SCHEMA" in _codes(findings)


def test_valid_skill_schema_clean(root):
    (root / "skills" / "debugging" / "SKILL.md").write_text(
        "---\nname: debugging\nscope: debugging\nstatus: active\nversion: 1\n---\n\nProcedure.\n"
    )
    findings = _lint(root)
    assert "INVALID_SKILL_SCHEMA" not in _codes(findings)


# ---------------------------------------------------------------------------
# Orphans / untouched (info level)
# ---------------------------------------------------------------------------


def test_orphan_source_info(root):
    # Second source never cited
    src_dir = root / "raw" / "sources" / "SRC-bbbbbbbbbbbb"
    src_dir.mkdir()
    src_dir.joinpath("source.md").write_text("# Orphan\n\nNot cited.\n")
    src_dir.joinpath("metadata.yaml").write_text(
        "source_id: SRC-bbbbbbbbbbbb\nsha256: " + _sha(src_dir / "source.md") + "\n"
    )
    findings = _lint(root)
    assert any(f.code == "ORPHAN_SOURCE" and "SRC-bbbbbbbbbbbb" in f.message
               for f in findings)


def test_untouched_knowledge_info(root):
    (root / "logs" / "retrieval.jsonl").write_text(
        '{"query": "x", "results": [{"path": "knowledge/concepts/valid.md", "score": 1}]}\n'
    )
    (root / "knowledge" / "concepts" / "fresh.md").write_text(
        "---\ntitle: Fresh\nkind: concept\nstatus: provisional\n"
        "source_refs:\n  - SRC-aaaaaaaaaaaa\n---\n\nNever retrieved.\n"
    )
    findings = _lint(root)
    assert any(f.code == "UNTOUCHED_KNOWLEDGE" and "fresh.md" in f.path
               for f in findings)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def test_summary_counts(root):
    (root / "knowledge" / "concepts" / "broken.md").write_text(
        "---\ntitle: Broken\nkind: concept\nstatus: provisional\n"
        "source_refs:\n  - SRC-deadbeef0000\n---\n\nContent.\n"
    )
    findings = _lint(root)
    summary = BrainLint(root=root).summary(findings)
    assert summary["errors"] >= 1
    assert "BROKEN_SOURCE_REF" in summary["by_code"]


if __name__ == "__main__":
    import pytest as _pytest

    _pytest.main([__file__, "-v"])

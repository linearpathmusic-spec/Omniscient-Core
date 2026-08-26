"""
Super Brain — Maintenance & Health Checks (Phase 8)

Deep module: Pi sees BrainLint.run(). It owns the corpus-integrity checks
for a Brain that has been running for months: broken provenance, status
violations, duplicate concepts, stale links, source integrity, orphans,
skill drift, oversized pages.

Karpathy's lint concept: report, don't auto-fix. Read-only. No daemon.
Phase 8 deliberately stays a tool, not a public brain_* agent capability —
it becomes one only if operational pain demands it.

Checks (severity: error | warning | info):
    BROKEN_SOURCE_REF      knowledge cites a source that does not exist
    SOURCE_INTEGRITY       raw source bytes no longer match metadata hash
    INVALID_STATUS         knowledge status outside provisional/verified/disputed
    VERIFIED_WITHOUT_EVIDENCE  verified page with empty source_refs (Phase 6 rule)
    DUPLICATE_TITLE        normalized-title collision in knowledge/
    STALE_WIKILINK         [[wikilink]] targets a nonexistent file
    INVALID_SKILL_SCHEMA   skill missing/invalid required frontmatter
    OVERSIZED_PAGE         knowledge page exceeds size limit (warning)
    ORPHAN_SOURCE          raw source not cited by any knowledge page (info)
    UNTOUCHED_KNOWLEDGE    knowledge page never returned by retrieval (info)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("brain.lint")

from brain.runtime.search import parse_frontmatter  # noqa: E402  # pyright: ignore[reportMissingImports]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_ROOT = Path(__file__).resolve().parent.parent.parent

KNOWLEDGE_ROOT = BRAIN_ROOT / "knowledge"

RAW_SOURCES = BRAIN_ROOT / "raw" / "sources"

SKILLS_ROOT = BRAIN_ROOT / "skills"

LOGS_DIR = BRAIN_ROOT / "logs"

ALLOWED_STATUSES = frozenset({"provisional", "verified", "disputed"})

ALLOWED_SKILL_SCOPES = frozenset({"research", "coding", "debugging", "planning", "security", "operations"})

REQUIRED_SKILL_FIELDS = frozenset({"name", "scope", "status", "version"})

MAX_PAGE_BYTES = 200 * 1024  # 200 KB

SEVERITIES = ("error", "warning", "info")


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One lint finding."""
    severity: str  # error | warning | info
    code: str
    path: str
    message: str


# ---------------------------------------------------------------------------
# BrainLint — deep module
# ---------------------------------------------------------------------------


class BrainLint:
    """Deep module owning all corpus health checks behind one interface."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or BRAIN_ROOT
        self.knowledge_root = self.root / "knowledge"
        self.raw_sources = self.root / "raw" / "sources"
        self.skills_root = self.root / "skills"
        self.logs_dir = self.root / "logs"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> list[Finding]:
        """Run all checks and return findings (unsorted)."""
        findings: list[Finding] = []
        findings += self._check_broken_source_refs()
        findings += self._check_source_integrity()
        findings += self._check_status_violations()
        findings += self._check_duplicate_titles()
        findings += self._check_stale_wikilinks()
        findings += self._check_oversized_pages()
        findings += self._check_skill_schema()
        findings += self._check_orphan_sources()
        findings += self._check_untouched_knowledge()
        return findings

    def summary(self, findings: list[Finding]) -> dict:
        """Count findings by severity and code."""
        return {
            "errors": sum(1 for f in findings if f.severity == "error"),
            "warnings": sum(1 for f in findings if f.severity == "warning"),
            "info": sum(1 for f in findings if f.severity == "info"),
            "by_code": {code: sum(1 for f in findings if f.code == code)
                        for code in sorted({f.code for f in findings})},
        }

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def _iter_knowledge(self) -> list[Path]:
        if not self.knowledge_root.exists():
            return []
        return sorted(self.knowledge_root.rglob("*.md"))

    def _iter_raw_sources(self) -> list[Path]:
        if not self.raw_sources.exists():
            return []
        return sorted(self.raw_sources.glob("SRC-*/metadata.yaml"))

    def _load_frontmatter(self, path: Path) -> dict:
        try:
            fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            return fm
        except Exception:
            return {}

    def _load_metadata(self, path: Path) -> dict:
        """Load raw-source metadata.yaml (plain key: value, no '---')."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return {}
        fm, _ = parse_frontmatter(text)
        if fm:
            return fm
        data: dict = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip()
        return data

    def _check_broken_source_refs(self) -> list[Finding]:
        findings: list[Finding] = []
        for page in self._iter_knowledge():
            fm = self._load_frontmatter(page)
            for ref in fm.get("source_refs", []) or []:
                if not isinstance(ref, str) or not ref.startswith("SRC-"):
                    findings.append(Finding(
                        "error", "BROKEN_SOURCE_REF", str(page),
                        f"invalid source ref {ref!r}",
                    ))
                    continue
                if not (self.raw_sources / ref / "metadata.yaml").exists():
                    findings.append(Finding(
                        "error", "BROKEN_SOURCE_REF", str(page),
                        f"cites missing source {ref}",
                    ))
        return findings

    def _check_source_integrity(self) -> list[Finding]:
        """Every raw source's bytes must match its metadata hash."""
        findings: list[Finding] = []
        for meta_path in self._iter_raw_sources():
            source_id = meta_path.parent.name
            fm = self._load_metadata(meta_path)
            expected = fm.get("sha256", "")
            source_file = meta_path.parent / "source.md"
            if not source_file.exists():
                findings.append(Finding(
                    "error", "SOURCE_INTEGRITY", str(meta_path.parent),
                    f"{source_id}: source.md missing",
                ))
                continue
            if not expected or len(expected) != 64:
                findings.append(Finding(
                    "error", "SOURCE_INTEGRITY", str(meta_path),
                    f"{source_id}: metadata sha256 missing or malformed",
                ))
                continue
            actual = hashlib.sha256(source_file.read_bytes()).hexdigest()
            if actual != expected:
                findings.append(Finding(
                    "error", "SOURCE_INTEGRITY", str(source_file),
                    f"{source_id}: hash mismatch (metadata {expected[:12]}... "
                    f"actual {actual[:12]}...)",
                ))
        return findings

    def _check_status_violations(self) -> list[Finding]:
        findings: list[Finding] = []
        for page in self._iter_knowledge():
            fm = self._load_frontmatter(page)
            status = fm.get("status", "")
            if status not in ALLOWED_STATUSES:
                findings.append(Finding(
                    "error", "INVALID_STATUS", str(page),
                    f"status {status!r} not in {sorted(ALLOWED_STATUSES)}",
                ))
            elif status == "verified" and not fm.get("source_refs"):
                # Phase 6: verified requires evidence + a review record
                findings.append(Finding(
                    "error", "VERIFIED_WITHOUT_EVIDENCE", str(page),
                    "verified but source_refs is empty (must be provisional)",
                ))
        return findings

    def _check_duplicate_titles(self) -> list[Finding]:
        findings: list[Finding] = []
        seen: dict[str, list[Path]] = {}
        for page in self._iter_knowledge():
            fm = self._load_frontmatter(page)
            title = " ".join(str(fm.get("title", "")).lower().split())
            if title:
                seen.setdefault(title, []).append(page)
        for title, pages in seen.items():
            if len(pages) > 1:
                findings.append(Finding(
                    "warning", "DUPLICATE_TITLE", str(pages[0]),
                    f"normalized title {title!r} used by "
                    + ", ".join(str(p.relative_to(self.root)) for p in pages),
                ))
        return findings

    def _check_stale_wikilinks(self) -> list[Finding]:
        """[[target]] links inside knowledge/ must resolve to a file."""
        findings: list[Finding] = []
        pattern = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
        for page in self._iter_knowledge():
            text = page.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                target = match.group(1).strip()
                if not target:
                    continue
                if target.startswith("http://") or target.startswith("https://"):
                    continue
                candidates = [
                    self.knowledge_root / f"{target}.md",
                    self.root / f"{target}.md",
                    self.knowledge_root / target,
                ]
                if not any(c.exists() for c in candidates):
                    findings.append(Finding(
                        "warning", "STALE_WIKILINK", str(page),
                        f"[[{target}]] resolves to no file",
                    ))
        return findings

    def _check_oversized_pages(self) -> list[Finding]:
        findings: list[Finding] = []
        for page in self._iter_knowledge():
            size = page.stat().st_size
            if size > MAX_PAGE_BYTES:
                findings.append(Finding(
                    "warning", "OVERSIZED_PAGE", str(page),
                    f"{size / 1024:.0f} KB exceeds {MAX_PAGE_BYTES // 1024} KB",
                ))
        return findings

    def _check_skill_schema(self) -> list[Finding]:
        findings: list[Finding] = []
        if not self.skills_root.exists():
            return findings
        for skill in sorted(self.skills_root.rglob("SKILL.md")):
            fm = self._load_frontmatter(skill)
            missing = REQUIRED_SKILL_FIELDS - set(fm.keys())
            if missing:
                findings.append(Finding(
                    "error", "INVALID_SKILL_SCHEMA", str(skill),
                    f"missing fields {sorted(missing)}",
                ))
                continue
            if fm.get("scope") not in ALLOWED_SKILL_SCOPES:
                findings.append(Finding(
                    "error", "INVALID_SKILL_SCHEMA", str(skill),
                    f"unknown scope {fm.get('scope')!r}",
                ))
            try:
                if int(fm.get("version", 0)) < 1:
                    findings.append(Finding(
                        "error", "INVALID_SKILL_SCHEMA", str(skill),
                        f"version must be >= 1, got {fm.get('version')!r}",
                    ))
            except (TypeError, ValueError):
                findings.append(Finding(
                    "error", "INVALID_SKILL_SCHEMA", str(skill),
                    f"version not an integer: {fm.get('version')!r}",
                ))
        return findings

    def _check_orphan_sources(self) -> list[Finding]:
        """Raw sources never cited by any knowledge page (info only)."""
        cited: set[str] = set()
        for page in self._iter_knowledge():
            cited.update(self._load_frontmatter(page).get("source_refs", []) or [])
        findings: list[Finding] = []
        for meta_path in self._iter_raw_sources():
            source_id = meta_path.parent.name
            if source_id not in cited:
                findings.append(Finding(
                    "info", "ORPHAN_SOURCE", str(meta_path.parent),
                    f"{source_id} not cited by any knowledge page",
                ))
        return findings

    def _check_untouched_knowledge(self) -> list[Finding]:
        """Knowledge pages never returned by any logged query (info only)."""
        log_path = self.logs_dir / "retrieval.jsonl"
        seen: set[str] = set()
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for result in record.get("results", []):
                    path = result.get("path", "")
                    if path:
                        seen.add(str(Path(path).as_posix()))
        findings: list[Finding] = []
        for page in self._iter_knowledge():
            rel = str(page.relative_to(self.root)).replace("\\", "/")
            if rel not in seen:
                findings.append(Finding(
                    "info", "UNTOUCHED_KNOWLEDGE", rel,
                    "never returned by a logged query",
                ))
        return findings

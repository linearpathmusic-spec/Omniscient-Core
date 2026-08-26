#!/usr/bin/env python3
"""
Super Brain — Operational Metrics

Counts and health signals for a running Brain: memory volume by type,
epistemic state distribution, skill/proposal counts, source counts,
and log sizes. Read-only. Used by /brain status.

Usage:
    python tools/brain_status.py
    python tools/brain_status.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BRAIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN_ROOT))

from brain.runtime.search import parse_frontmatter  # noqa: E402  # pyright: ignore[reportMissingImports]

LOGS = ("retrieval.jsonl", "reviews.jsonl", "writes.jsonl",
        "ingestion.jsonl", "memory.jsonl", "skills.jsonl")


def _frontmatter(path: Path) -> dict:
    try:
        fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        return fm
    except Exception:
        return {}


def collect() -> dict:
    status: dict = {"counts": {}, "logs": {}, "states": {}}

    # Knowledge pages by epistemic state
    knowledge = sorted((BRAIN_ROOT / "knowledge").rglob("*.md"))
    status["counts"]["knowledge_pages"] = len(knowledge)
    for page in knowledge:
        st = _frontmatter(page).get("status", "unknown")
        status["states"][st] = status["states"].get(st, 0) + 1

    # Raw sources
    raw = BRAIN_ROOT / "raw" / "sources"
    status["counts"]["raw_sources"] = len(list(raw.glob("SRC-*/metadata.yaml"))) if raw.exists() else 0

    # Skills + proposals
    skills = sorted((BRAIN_ROOT / "skills").rglob("SKILL.md"))
    status["counts"]["skills"] = len(skills)
    pending = BRAIN_ROOT / "proposals" / "skills" / "pending"
    resolved = BRAIN_ROOT / "proposals" / "skills" / "resolved"
    status["counts"]["proposals_pending"] = len(list(pending.glob("*.md"))) if pending.exists() else 0
    status["counts"]["proposals_resolved"] = len(list(resolved.glob("*.md"))) if resolved.exists() else 0

    # Memory by type
    for sub, kind in (("decisions", "decisions"), ("lessons", "lessons"), ("episodes", "episodes")):
        d = BRAIN_ROOT / "history" / sub
        status["counts"][kind] = len(list(d.glob("*.md"))) if d.exists() else 0

    # Log volumes
    for name in LOGS:
        p = BRAIN_ROOT / "logs" / name
        status["logs"][name] = len(p.read_text().splitlines()) if p.exists() else 0

    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Super Brain — operational metrics")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    data = collect()

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    print("Super Brain status")
    print("=" * 40)
    for key, value in data["counts"].items():
        print(f"  {key:22} {value}")
    print("\n  knowledge states:")
    for state, count in sorted(data["states"].items()):
        print(f"    {state:12} {count}")
    print("\n  log volumes:")
    for name, count in data["logs"].items():
        print(f"    {name:20} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

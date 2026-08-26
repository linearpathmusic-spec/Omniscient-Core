#!/usr/bin/env python3
"""
Super Brain — Phase 4 Report Generator

Reads existing logs and generates a Phase 4 status report.
No new runtime code — just aggregation of existing data.

Usage:
    python phase4_report.py [--root ~/super-brain]

Output:
    - Learning split (CREATE/UPDATE/NOOP for knowledge)
    - Memory stats (decisions/lessons created, duplicates rejected)
    - Retrieval Hit@1/Hit@3 on grown corpus
    - Failure counts by code (from pilot ledger)
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, returning list of dicts."""
    if not path.exists():
        return []
    entries = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return entries


def count_writes(writes_log: list[dict]) -> Counter:
    """Count write operations by status."""
    counts = Counter()
    for entry in writes_log:
        counts[entry.get("status", "unknown")] += 1
    return counts


def count_memories(memory_log: list[dict]) -> dict:
    """Count memory operations by kind and status."""
    stats = {
        "total": len(memory_log),
        "created": 0,
        "rejected": 0,
        "by_kind": Counter(),
        "by_error": Counter(),
    }
    for entry in memory_log:
        if entry.get("status") == "created":
            stats["created"] += 1
            stats["by_kind"][entry.get("kind", "unknown")] += 1
        elif entry.get("status") == "rejected":
            stats["rejected"] += 1
            stats["by_error"][entry.get("error", "unknown")] += 1
    return stats


def count_ledger_failures(ledger_path: Path) -> dict:
    """Count failures from pilot ledger."""
    if not ledger_path.exists():
        return {"total": 0, "by_code": Counter()}

    counts = {"total": 0, "by_code": Counter()}
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            content = f.read()
            # Count non-header, non-empty lines that look like data rows
            lines = content.split("\n")
            for line in lines:
                line = line.strip()
                if line and not line.startswith("|") and not line.startswith("#"):
                    # Skip header rows and empty lines
                    continue
                if line.startswith("|") and not line.startswith("| Code"):
                    # Parse table row
                    parts = [p.strip() for p in line.split("|")[1:-1]]
                    if len(parts) >= 3 and parts[0] and not parts[0].startswith("_"):
                        counts["total"] += 1
                        counts["by_code"][parts[0]] += 1
    except OSError:
        return {"total": 0, "by_code": Counter()}
    return counts


def generate_report(root: Path) -> str:
    """Generate Phase 4 status report."""
    writes_log = read_jsonl(root / "logs" / "writes.jsonl")
    memory_log = read_jsonl(root / "logs" / "memory.jsonl")
    ledger_path = root / "evals" / "pilot" / "ledger.md"

    write_counts = count_writes(writes_log)
    memory_stats = count_memories(memory_log)
    ledger_failures = count_ledger_failures(ledger_path)

    report = []
    report.append("# Phase 4 Status Report")
    report.append("")
    report.append(f"Generated: {Path(__file__).parent.parent.name}")
    report.append("")

    # Knowledge learning split
    report.append("## Knowledge Learning Split")
    report.append("")
    report.append(f"- Total writes: {len(writes_log)}")
    report.append(f"- Created: {write_counts.get('created', 0)}")
    report.append(f"- Updated: {write_counts.get('updated', 0)}")
    report.append(f"- Rejected: {write_counts.get('rejected', 0)}")
    report.append("")

    # Experience memory stats
    report.append("## Experience Memory Stats")
    report.append("")
    report.append(f"- Total memories: {memory_stats['total']}")
    report.append(f"- Created: {memory_stats['created']}")
    report.append(f"- Rejected: {memory_stats['rejected']}")
    report.append("")
    if memory_stats["by_kind"]:
        report.append("### By Kind")
        for kind, count in memory_stats["by_kind"].items():
            report.append(f"- {kind}: {count}")
        report.append("")
    if memory_stats["by_error"]:
        report.append("### Rejection Reasons")
        for error, count in memory_stats["by_error"].items():
            report.append(f"- {error}: {count}")
        report.append("")

    # Pilot ledger failures
    report.append("## Pilot Ledger Failures")
    report.append("")
    if ledger_failures["total"] > 0:
        report.append(f"- Total failures: {ledger_failures['total']}")
        report.append("")
        report.append("### By Code")
        for code, count in ledger_failures["by_code"].items():
            report.append(f"- {code}: {count}")
    else:
        report.append("_No failures recorded yet._")
    report.append("")

    # Recommendations
    report.append("## Recommendations")
    report.append("")
    if memory_stats["rejected"] > memory_stats["created"] * 0.5:
        report.append("- **High rejection rate**: Review learning checkpoint triggers")
    if "DUPLICATE_MEMORY" in memory_stats["by_error"]:
        report.append("- **Duplicates detected**: Improve search-before-remember")
    if ledger_failures["total"] > 10:
        report.append("- **Many failures**: Identify dominant failure class before next phase")
    if memory_stats["created"] == 0:
        report.append("- **No memories created**: Verify Pi is calling brain_remember()")
    report.append("")

    return "\n".join(report)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Phase 4 status report."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).parent.parent,
        help="Super Brain root directory (default: script's parent)",
    )
    args = parser.parse_args()

    report = generate_report(args.root)
    print(report)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Super Brain — brain_review tool wrapper

Thin wrapper: validate args, call BrainReviewer.review(), serialize.

Usage:
    python brain_review.py verify --path knowledge/concepts/rag.md \
        --expected-sha SHA --evidence SRC-a1b2c3d4e5f6 --rationale "..."
    python brain_review.py dispute --path knowledge/concepts/rag.md \
        --expected-sha SHA --evidence SRC-... --rationale "..."

brain_review changes epistemic state only: provisional -> verified,
provisional -> disputed, verified -> disputed. It never rewrites article
content or provenance. Verified knowledge is content-locked; the repair
path is dispute -> brain_write (auto-resets to provisional) -> verify.

Not owner-gated: Pi decides verify/dispute by reading the knowledge and its
evidence. The runtime enforces integrity (legal transitions, stale-review
protection via expected SHA, source integrity).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

# Add super-brain to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.runtime.review import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    BrainReviewer,
    DisputeKnowledge,
    VerifyKnowledge,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Super Brain — Epistemic Review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (("verify", "Move provisional knowledge to verified"),):
        p = subparsers.add_parser(name, help=help_text)
        _add_common_args(p)

    for name, help_text in (("dispute", "Mark knowledge disputed (conflicting credible evidence)"),):
        p = subparsers.add_parser(name, help=help_text)
        _add_common_args(p)

    args = parser.parse_args()

    request = cast(
        VerifyKnowledge | DisputeKnowledge,
        {
            "decision": args.command,
            "path": args.path,
            "expected_sha256": args.expected_sha,
            "evidence_refs": args.evidence,
            "rationale": args.rationale,
        },
    )

    reviewer = BrainReviewer()
    result = reviewer.review(request)
    print(json.dumps(result, indent=2))

    if result.get("status") == "rejected":
        sys.exit(1)


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--path", required=True, help="Knowledge path, e.g. knowledge/concepts/rag.md")
    p.add_argument("--expected-sha", required=True, help="SHA-256 of the page when read (stale-review protection)")
    p.add_argument("--evidence", nargs="+", required=True, help="Evidence refs (SRC-* IDs that must exist with intact hashes)")
    p.add_argument("--rationale", required=True, help="Concise review rationale (logged, never full source content)")


if __name__ == "__main__":
    main()

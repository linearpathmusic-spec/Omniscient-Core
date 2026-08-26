#!/usr/bin/env python3
"""
Super Brain — brain_write tool wrapper

Thin wrapper: validate args, call BrainWriter.write(), serialize.

Usage:
    python brain_write.py --op create --path knowledge/concepts/rag.md \
        --content-file /tmp/rag-content.md
    python brain_write.py --op update --path knowledge/concepts/rag.md \
        --content-file /tmp/rag-content.md --expected-sha 9812...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BRAIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN_ROOT))

from brain.runtime.write import BrainWriter, CreateKnowledge, UpdateKnowledge  # noqa: E402  # pyright: ignore[reportMissingImports]


def brain_write(
    op: str,
    path: str,
    content: str,
    expected_sha256: str | None = None,
) -> dict:
    """
    Public API: safely write one knowledge document.

    Args:
        op: "create" or "update".
        path: Repo-relative target path (must start with knowledge/).
        content: Full markdown with YAML frontmatter (title, kind, source_refs, status).
        expected_sha256: Required for update (optimistic concurrency).

    Returns:
        JSON-serializable write result.
    """
    writer = BrainWriter(BRAIN_ROOT)

    if op == "create":
        request: CreateKnowledge | UpdateKnowledge = CreateKnowledge(
            op="create",
            path=path,
            content=content,
            source_refs=[],  # writer reads source_refs from frontmatter
        )
    elif op == "update":
        if not expected_sha256:
            raise ValueError("INVALID_REQUEST: update requires --expected-sha")
        request = UpdateKnowledge(
            op="update",
            path=path,
            content=content,
            source_refs=[],  # writer reads source_refs from frontmatter
            expected_sha256=expected_sha256,
        )
    else:
        raise ValueError(f"INVALID_REQUEST: unknown op: {op}")

    return dict(writer.write(request))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write knowledge to the Super Brain."
    )
    parser.add_argument(
        "--op", "-o", required=True, choices=["create", "update"],
        help="Operation: create or update",
    )
    parser.add_argument(
        "--path", "-p", required=True,
        help="Repo-relative target path, e.g. knowledge/concepts/rag.md",
    )
    parser.add_argument(
        "--content-file", "-f", default=None,
        help="Path to a file containing the full markdown content (frontmatter + body)",
    )
    parser.add_argument(
        "--content", "-c", default=None,
        help="Inline markdown content (alternative to --content-file)",
    )
    parser.add_argument(
        "--expected-sha", "-e", default=None,
        help="Required for update: the SHA-256 the file had when read",
    )

    args = parser.parse_args()

    if args.content_file and args.content:
        print(json.dumps({"error": "Provide either --content-file or --content, not both"}))
        sys.exit(1)

    if not args.content_file and not args.content:
        print(json.dumps({"error": "Provide either --content-file or --content"}))
        sys.exit(1)

    content = (
        Path(args.content_file).read_text(encoding="utf-8")
        if args.content_file
        else args.content
    )

    try:
        result = brain_write(
            op=args.op,
            path=args.path,
            content=content,
            expected_sha256=args.expected_sha,
        )
        print(json.dumps(result, indent=2))
    except ValueError as e:
        print(json.dumps({"error": str(e)}, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

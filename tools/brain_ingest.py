#!/usr/bin/env python3
"""
Super Brain — brain_ingest tool wrapper

Thin wrapper: validate args, call BrainWriter.ingest(), serialize.

Usage:
    python brain_ingest.py --source /path/to/file.md
    python brain_ingest.py --source /path/to/file.md --project super-brain
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BRAIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN_ROOT))

from brain.runtime.write import BrainWriter  # noqa: E402  # pyright: ignore[reportMissingImports]


def brain_ingest(
    source_path: str,
    project: str | None = None,
    authority: str | None = None,
) -> dict:
    """
    Public API: preserve a source in the Brain.

    Args:
        source_path: Path to the source file.
        project: Optional project scope.
        authority: Categorical source authority (primary | secondary | unknown).

    Returns:
        JSON-serializable ingest result.
    """
    writer = BrainWriter(BRAIN_ROOT)
    if authority is None:
        result = writer.ingest(source_path, project)
    else:
        result = writer.ingest(source_path, project, authority)
    return dict(result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest a source into the Super Brain."
    )
    parser.add_argument(
        "--source", "-s", required=True, help="Path to source file"
    )
    parser.add_argument(
        "--project", "-p", default=None, help="Optional project scope"
    )
    parser.add_argument(
        "--authority", "-a", default=None,
        choices=["primary", "secondary", "unknown"],
        help="Explicit source authority; omitted preserves an existing classification",
    )

    args = parser.parse_args()

    try:
        result = brain_ingest(
            source_path=args.source,
            project=args.project,
            authority=args.authority,
        )
        print(json.dumps(result, indent=2))
    except ValueError as e:
        print(json.dumps({"error": str(e)}, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

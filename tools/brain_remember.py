#!/usr/bin/env python3
"""
Super Brain — brain_remember tool wrapper

Thin wrapper: validate args, call BrainMemory.remember(), serialize.

Usage:
    python brain_remember.py --kind decision --project super-brain \
        --title "Keep lexical retrieval" --decision "..." \
        --rationale "..." --alternatives '["BM25", "embeddings"]'
    python brain_remember.py --kind lesson --project super-brain \
        --title "Runtime paths must respect instance root" \
        --lesson "..." --learned-from "..."
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import cast
from pathlib import Path

BRAIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN_ROOT))

from brain.runtime.remember import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    BrainMemory,
    RememberDecision,
    RememberLesson,
)


def brain_remember(
    kind: str,
    project: str,
    title: str,
    decision: str | None = None,
    rationale: str | None = None,
    alternatives: list[str] | None = None,
    lesson: str | None = None,
    learned_from: str | None = None,
    context_refs: list[str] | None = None,
) -> dict:
    """
    Public API: remember a decision or lesson.

    Args:
        kind: "decision" or "lesson".
        project: Project name (e.g., "super-brain").
        title: Memory title.
        decision: Required for kind="decision".
        rationale: Required for kind="decision".
        alternatives: Required for kind="decision".
        lesson: Required for kind="lesson".
        learned_from: Required for kind="lesson".
        context_refs: Optional list of context references.

    Returns:
        JSON-serializable memory result.
    """
    memory = BrainMemory(BRAIN_ROOT)

    if kind == "decision":
        if not all([decision, rationale, alternatives]):
            raise ValueError(
                "INVALID_REQUEST: decision requires --decision, --rationale, "
                "and --alternatives"
            )
        request: RememberDecision | RememberLesson = RememberDecision(
            kind="decision",
            project=project,
            title=title,
            decision=cast(str, decision),
            rationale=cast(str, rationale),
            alternatives=alternatives or [],
            context_refs=context_refs or [],
        )
    elif kind == "lesson":
        if not all([lesson, learned_from]):
            raise ValueError(
                "INVALID_REQUEST: lesson requires --lesson and --learned-from"
            )
        request = RememberLesson(
            kind="lesson",
            project=project,
            title=title,
            lesson=cast(str, lesson),
            learned_from=cast(str, learned_from),
            context_refs=context_refs or [],
        )
    else:
        raise ValueError(f"INVALID_REQUEST: unknown kind: {kind}")

    return dict(memory.remember(request))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remember a decision or lesson to the Super Brain."
    )
    parser.add_argument(
        "--kind", "-k", required=True, choices=["decision", "lesson"],
        help="Memory kind: decision or lesson",
    )
    parser.add_argument(
        "--project", "-p", required=True,
        help="Project name (e.g., super-brain)",
    )
    parser.add_argument(
        "--title", "-t", required=True,
        help="Memory title",
    )
    parser.add_argument(
        "--decision", "-d", default=None,
        help="The decision (required for kind=decision)",
    )
    parser.add_argument(
        "--rationale", "-r", default=None,
        help="Rationale for the decision (required for kind=decision)",
    )
    parser.add_argument(
        "--alternatives", "-a", default=None,
        help="JSON list of alternatives considered (required for kind=decision)",
    )
    parser.add_argument(
        "--lesson", "-l", default=None,
        help="The lesson learned (required for kind=lesson)",
    )
    parser.add_argument(
        "--learned-from", default=None,
        help="What experience taught this lesson (required for kind=lesson)",
    )
    parser.add_argument(
        "--context-refs", default=None,
        help="JSON list of context references (optional)",
    )

    args = parser.parse_args()

    alternatives = None
    if args.alternatives:
        try:
            alternatives = json.loads(args.alternatives)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"Invalid JSON for --alternatives: {e}"}))
            sys.exit(1)

    context_refs = None
    if args.context_refs:
        try:
            context_refs = json.loads(args.context_refs)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"Invalid JSON for --context-refs: {e}"}))
            sys.exit(1)

    result = brain_remember(
        kind=args.kind,
        project=args.project,
        title=args.title,
        decision=args.decision,
        rationale=args.rationale,
        alternatives=alternatives,
        lesson=args.lesson,
        learned_from=args.learned_from,
        context_refs=context_refs,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

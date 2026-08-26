#!/usr/bin/env python3
"""
Super Brain — brain_query tool wrapper

Thin wrapper: validate args, call BrainSearch, assign query ID, log, serialize.

Usage:
    python brain_query.py --query "Why did we choose hybrid routing?"
    python brain_query.py --query "Explain RAG" --top-k 3 --project super-brain
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure super-brain root is on the path
BRAIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN_ROOT))

from brain.runtime.search import BrainSearch, QueryResponse  # noqa: E402


def brain_query(
    query: str,
    top_k: int = 5,
    project: str | None = None,
) -> dict:
    """
    Public API: search persistent Super Brain knowledge.

    Args:
        query: Natural-language search query.
        top_k: Optional result count (default 5).
        project: Optional project scope.

    Returns:
        JSON-serializable response dict.
    """
    engine = BrainSearch(BRAIN_ROOT)
    response = engine.query(query=query, top_k=top_k, project=project)
    return _serialize(response)


def _serialize(response: QueryResponse) -> dict:
    """Convert QueryResponse to JSON-serializable dict."""
    return {
        "query_id": response.query_id,
        "query": response.query,
        "results": [
            {
                "id": r.id,
                "title": r.title,
                "kind": r.kind,
                "project": r.project,
                "status": r.status,
                "path": r.path,
                "score": r.score,
                "snippet": r.snippet,
                "source_refs": r.source_refs,
            }
            for r in response.results
        ],
        "result_count": response.result_count,
    } | ({
        "warning": response.warning,
    } if response.warning else {})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search the Super Brain knowledge base."
    )
    parser.add_argument(
        "--query", "-q", required=True, help="Search query"
    )
    parser.add_argument(
        "--top-k", "-k", type=int, default=5,
        help="Number of results (default: 5)"
    )
    parser.add_argument(
        "--project", "-p", default=None,
        help="Optional project scope"
    )
    parser.add_argument(
        "--log", action="store_true",
        help="Also write to retrieval log"
    )

    args = parser.parse_args()

    result = brain_query(
        query=args.query,
        top_k=args.top_k,
        project=args.project,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

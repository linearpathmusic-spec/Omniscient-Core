#!/usr/bin/env python3
"""
Super Brain — thin MCP adapter.

Transport layer only. No business logic, no alternate validation, storage, or
routing. Each MCP tool handler calls an existing tools/* wrapper verbatim:

    MCP request -> FastMCP validates the input schema
                 -> call the existing tools.<name>() function
                 -> serialize the returned dict

This keeps the Brain's abstraction intact: MCP exposes the Brain's existing
interfaces, it does not build a parallel one.

Trust gating (single explicit signal, BRAIN_MCP_TRUSTED):
    brain_query   -> always enabled (safe read)
    brain_ingest  -> trusted local client only
    brain_write   -> trusted local client only

Any MCP-capable client can call a server, so mutating tools are off unless the
launching client opts in. Fail safe: any value other than 1/true/yes is untrusted.

Launch:
    BRAIN_MCP_TRUSTED=1 python tools/brain_mcp.py      # stdio (default)
    BRAIN_MCP_TRUSTED=1 BRAIN_MCP_TRANSPORT=sse python tools/brain_mcp.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BRAIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402  # type: ignore[reportMissingImports]

# Existing interfaces — imported and called verbatim. No re-implementation.
from tools.brain_query import brain_query  # noqa: E402
from tools.brain_ingest import brain_ingest  # noqa: E402
from tools.brain_write import brain_write  # noqa: E402


def _trusted() -> bool:
    """Mutating tools require an explicit trust signal (trusted local client)."""
    return os.environ.get("BRAIN_MCP_TRUSTED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }


mcp = FastMCP("super-brain")


@mcp.tool(name="brain_query")
def _query_handler(
    query: str,
    top_k: int = 5,
    project: str | None = None,
) -> dict:
    """Search persistent Super Brain knowledge."""
    return brain_query(query=query, top_k=top_k, project=project)


if _trusted():

    @mcp.tool(name="brain_ingest")
    def _ingest_handler(
        source_path: str,
        project: str | None = None,
        authority: str | None = None,
    ) -> dict:
        """Preserve a raw source in the Brain (constrained evidence capture)."""
        return brain_ingest(
            source_path=source_path, project=project, authority=authority
        )

    @mcp.tool(name="brain_write")
    def _write_handler(
        op: str,
        path: str,
        content: str,
        expected_sha256: str | None = None,
    ) -> dict:
        """Write one knowledge document (durable mutation)."""
        return brain_write(
            op=op, path=path, content=content, expected_sha256=expected_sha256
        )


if __name__ == "__main__":
    transport = os.environ.get("BRAIN_MCP_TRANSPORT", "stdio").lower()
    match transport:
        case "stdio":
            mcp.run(transport="stdio")
        case "sse":
            mcp.run(transport="sse")
        case other:
            raise SystemExit(f"UNKNOWN_TRANSPORT: {other}")

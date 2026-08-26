#!/usr/bin/env python3
"""
Super Brain — brain_lint CLI

Run corpus health checks. Read-only: reports findings, never auto-fixes.

Usage:
    python tools/brain_lint.py            # full report
    python tools/brain_lint.py --errors   # errors only
    python tools/brain_lint.py --json     # machine-readable output

Exit codes:
    0  clean (or info-only findings)
    1  errors found
    2  warnings found (no errors)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BRAIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRAIN_ROOT))

from brain.runtime.lint import BrainLint  # noqa: E402  # pyright: ignore[reportMissingImports]

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def main() -> int:
    parser = argparse.ArgumentParser(description="Super Brain — corpus health checks")
    parser.add_argument("--errors", action="store_true", help="Show errors only")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    lint = BrainLint()
    findings = lint.run()
    summary = lint.summary(findings)

    if args.json:
        print(json.dumps({
            "summary": summary,
            "findings": [
                {"severity": f.severity, "code": f.code, "path": f.path, "message": f.message}
                for f in sorted(findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.path))
            ],
        }, indent=2))
    else:
        for f in sorted(findings, key=lambda x: (SEVERITY_ORDER[x.severity], x.path)):
            if args.errors and f.severity != "error":
                continue
            print(f"[{f.severity.upper():7}] {f.code:28} {f.path}: {f.message}")
        print()
        print(f"errors={summary['errors']} warnings={summary['warnings']} "
              f"info={summary['info']}")

    if summary["errors"]:
        return 1
    if summary["warnings"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

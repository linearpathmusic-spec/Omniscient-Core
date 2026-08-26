#!/usr/bin/env python3
"""
Super Brain — Skill Management CLI

Phase 5: Controlled Procedural Learning

Usage:
    brain_skill.py propose-create --skill-name NAME --purpose P --content FILE --evidence REF [REF...]
    brain_skill.py propose-update --skill-path PATH --expected-sha SHA --rationale R --content FILE --evidence REF [REF...]
    brain_skill.py approve --proposal-id ID --expected-sha SHA [--owner]
    brain_skill.py reject --proposal-id ID --reason REASON [--owner]

The --owner flag is required for approve/reject operations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

# Add super-brain to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.runtime.skills import (
    ApproveSkillProposal,
    BrainSkills,
    ProposeSkillCreate,
    ProposeSkillUpdate,
    RejectSkillProposal,
)


def read_file_content(path: str) -> str:
    """Read file content for --content argument."""
    return Path(path).read_text()


def main():
    parser = argparse.ArgumentParser(
        description="Super Brain — Skill Management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # propose-create
    create_parser = subparsers.add_parser("propose-create", help="Propose a new skill")
    create_parser.add_argument("--skill-name", required=True, help="Skill name (e.g., 'debugging')")
    create_parser.add_argument("--purpose", required=True, help="Purpose of the skill")
    create_parser.add_argument("--content", required=True, help="Path to proposed SKILL.md content")
    create_parser.add_argument("--evidence", nargs="+", required=True, help="Evidence refs (LES-*, DEC-*, SRC-*)")

    # propose-update
    update_parser = subparsers.add_parser("propose-update", help="Propose an update to existing skill")
    update_parser.add_argument("--skill-path", required=True, help="Skill path (e.g., 'debugging/SKILL.md')")
    update_parser.add_argument("--expected-sha", required=True, help="Expected SHA256 of current skill")
    update_parser.add_argument("--rationale", required=True, help="Rationale for update")
    update_parser.add_argument("--content", required=True, help="Path to proposed SKILL.md content")
    update_parser.add_argument("--evidence", nargs="+", required=True, help="Evidence refs")

    # approve
    approve_parser = subparsers.add_parser("approve", help="Approve a pending proposal (owner only)")
    approve_parser.add_argument("--proposal-id", required=True, help="Proposal ID (SKP-*)")
    approve_parser.add_argument("--expected-sha", required=True, help="Expected SHA256 of proposal file")
    approve_parser.add_argument("--owner", action="store_true", help="Owner authorization required")

    # reject
    reject_parser = subparsers.add_parser("reject", help="Reject a pending proposal (owner only)")
    reject_parser.add_argument("--proposal-id", required=True, help="Proposal ID (SKP-*)")
    reject_parser.add_argument("--reason", required=True, help="Reason for rejection")
    reject_parser.add_argument("--owner", action="store_true", help="Owner authorization required")

    args = parser.parse_args()

    # Initialize BrainSkills
    is_owner = getattr(args, "owner", False)
    brain = BrainSkills(is_owner=is_owner)

    # Execute command
    if args.command == "propose-create":
        content = read_file_content(args.content)
        request = cast(ProposeSkillCreate, {
            "op": "propose_create",
            "skill_name": args.skill_name,
            "purpose": args.purpose,
            "proposed_content": content,
            "evidence_refs": args.evidence,
        })
        result = brain.propose(request)

    elif args.command == "propose-update":
        content = read_file_content(args.content)
        request = cast(ProposeSkillUpdate, {
            "op": "propose_update",
            "skill_path": args.skill_path,
            "expected_sha256": args.expected_sha,
            "rationale": args.rationale,
            "proposed_content": content,
            "evidence_refs": args.evidence,
        })
        result = brain.propose(request)

    elif args.command == "approve":
        if not is_owner:
            print(json.dumps({
                "status": "rejected",
                "code": "UNAUTHORIZED_OPERATION",
                "message": "Approval requires --owner flag.",
            }))
            sys.exit(1)
        request = cast(ApproveSkillProposal, {
            "op": "approve",
            "proposal_id": args.proposal_id,
            "expected_proposal_sha256": args.expected_sha,
        })
        result = brain.approve(request)

    elif args.command == "reject":
        if not is_owner:
            print(json.dumps({
                "status": "rejected",
                "code": "UNAUTHORIZED_OPERATION",
                "message": "Rejection requires --owner flag.",
            }))
            sys.exit(1)
        request = cast(RejectSkillProposal, {
            "op": "reject",
            "proposal_id": args.proposal_id,
            "reason": args.reason,
        })
        result = brain.reject(request)

    else:
        print(json.dumps({"status": "rejected", "code": "INVALID_COMMAND", "message": f"Unknown command: {args.command}"}))
        sys.exit(1)

    # Output result
    print(json.dumps(result, indent=2))

    # Exit code based on status
    if result.get("status") == "rejected":
        sys.exit(1)


if __name__ == "__main__":
    main()

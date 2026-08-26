---
title: Brain Schema — File Formats and Conventions
---

# Brain Schema — File Formats and Conventions

## Raw Sources

Content-derived source IDs: `SRC-{sha256[:12]}`.

```yaml
---
source_id: SRC-a91f72b310ce
title: Document Title
type: pdf | web | book | paper | dataset | conversation
origin: https://... or /local/path
author: Author Name
published: YYYY-MM-DD
ingested_at: YYYY-MM-DDTHH:MM:SS-04:00
sha256: <full 64-char hex hash>
authority: primary | secondary | unknown
---

# Title

Content...
```

`authority` (Phase 6) is categorical source authority for review reasoning:
`primary` (official standard, original research, government law),
`secondary` (review article, technical blog), `unknown` (forum post, default).
Never a numeric score.

## Knowledge Pages

```yaml
---
title: Concept Name
kind: concept | entity | comparison | timeline | project
project: project-name | null
tags:
  - tag1
  - tag2
source_refs:
  - SRC-a91f72b310ce
status: provisional | verified | disputed
---

# Title

Content with inline citations [SRC-a91f72b310ce].
```

## Epistemic States (Phase 6)

Exactly three states — no confidence scores, no trust ratings:

| State | Meaning |
| --- | --- |
| `provisional` | learned, but not independently reviewed enough to rely on strongly (default) |
| `verified` | evidence has been reviewed and materially supports the knowledge |
| `disputed` | credible evidence conflicts with the current representation |

Legal transitions (enforced by `brain_review`):

```text
provisional -> verified
provisional -> disputed
verified    -> disputed
```

`disputed -> verified` directly is illegal. Correct path:
`brain_write` (corrected content, auto-resets to provisional) -> `brain_review`.
Verified knowledge is content-locked: `brain_write` rejects updates until the
page is disputed. `brain_review` changes status only — never content or
provenance.

## Episodic Memory

```yaml
---
type: episode | decision | lesson | preference | observation
date: YYYY-MM-DD
project: project-name
importance: 0.0-1.0
entities:
  - Entity1
  - Entity2
---

## Event / Decision / Lesson

Description.

## Reason

Why it mattered.

## Follow-up

What comes next.
```

## State Files

```yaml
---
active_project: project-name
current_goal: Goal description
current_phase: 1
next_action: Next step
blocked_by: null | dependency-name
---
```

## Skills

```yaml
---
name: Skill Name
trigger: When to use
category: research | coding | planning | debugging | ...
---

## Procedure

1. Step one
2. Step two
```

## Review Audit Log

Each review is appended to `logs/reviews.jsonl`:

```json
{
  "review_id": "BRV-a31cd9",
  "timestamp": "2026-08-22T...",
  "decision": "verify",
  "path": "knowledge/ai/rag.md",
  "previous_status": "provisional",
  "new_status": "verified",
  "content_sha256": "abc...",
  "evidence_refs": ["SRC-A", "SRC-B"],
  "rationale": "Core claims are materially supported by reviewed evidence."
}
```

Rejections carry `error: "<CODE>: <message>"`. Rationale stays concise;
full source content is never copied into review logs.

## Rules

- All dates: `YYYY-MM-DD` (ISO 8601 for timestamps)
- Source IDs: `SRC-{sha256[:12]}` (content-derived)
- Knowledge kinds: `concept`, `entity`, `comparison`, `timeline`, `project`
- Knowledge status: `provisional` (default), `verified`, `disputed`
- Tags: lowercase, hyphenated

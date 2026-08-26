---
title: Memory Policy
---

# Memory Policy — What Gets Remembered and When

## Memory Types and Lifecycles

### Episodic Memory (`memory/episodes/`)

**What:** Records of what happened — sessions, decisions, discoveries.

**When to save:**

- After any operation that took > 2 minutes
- After any decision with non-obvious tradeoffs
- After any bug fix that took > 5 minutes
- After any user preference discovered
- After any architectural decision

**When NOT to save:**

- Trivial one-liner operations
- Standard library usage
- Generic patterns (try/except, for loops)
- Anything obvious

**Lifecycle:**

- Active: current year/month
- Archive: older than 6 months (compressed into monthly summaries)
- Prune: older than 2 years (keep only importance > 0.7)

### Decisions (`memory/decisions/`)

**What:** Decisions with rationale, not just outcomes.

**Format:**

```yaml
---
type: decision
date: YYYY-MM-DD
project: project-name
confidence: high | medium | low
outcome: confirmed | mixed | wrong
---

## Decision

What was decided.

## Rationale

Why this choice was made. Alternatives considered.

## Outcome

What actually happened. Was the decision correct?
```

**Lifecycle:**

- Never pruned (decisions are always referenceable)
- Outcome updated when results are known

### Lessons (`memory/lessons/`)

**What:** Actionable knowledge from experience.

**When to save:**

- Error that took > 5 minutes to resolve
- Pattern that felt clever (document for future)
- Tool/framework gotcha (API changes, config quirks)
- Painful-debug fixes (> 5 min)
- Recurring patterns

**When NOT to save:**

- Trivial one-liners with no edge cases
- Standard library usage
- Generic patterns
- Anything obvious

**Lifecycle:**

- Active: seen_count > 0
- Promoted: seen_count >= 2 → becomes a skill
- Stale: 60 days inactive → marked stale
- Archived: 120 days inactive → archived

### Preferences (`memory/preferences/`)

**What:** User preferences discovered through interaction.

**Format:**

```yaml
---
type: preference
date: YYYY-MM-DD
domain: coding | writing | design | communication | ...
confidence: high | medium | low
---

## Preference

What the user prefers.

## Evidence

How we know (user said it, observed pattern).

## Examples

Concrete examples of the preference in action.
```

**Lifecycle:**

- Updated when new evidence contradicts old preference
- Never pruned (preferences are stable)

### Observations (`memory/observations/`)

**What:** Temporary observations that may become lessons.

**Format:**

```yaml
---
type: observation
date: YYYY-MM-DD
confidence: low | medium
---

## Observation

What was noticed.

## Significance

Why it might matter.

## Follow-up

What to watch for.
```

**Lifecycle:**

- Promoted to lesson if seen again
- Archived after 30 days if not reinforced
- Deleted after 60 days if not promoted

## Memory Hierarchy

```
Observation (temporary, low confidence)
    ↓ reinforced
Lesson (actionable, medium confidence)
    ↓ recurring
Skill (procedural, high confidence)
    ↓ verified
Wiki knowledge (factual, verified)
```

## Forgetting Policy

A brain needs selective forgetting as much as remembering.

| Memory Type | Retention | Archive | Delete |
| --- | --- | --- | --- |
| Decisions | Forever | Never | Never |
| Preferences | Forever | Never | Never |
| Lessons | 2 years | 6 months | 2 years |
| Episodes | 1 year | 6 months | 2 years |
| Observations | 30 days | 30 days | 60 days |

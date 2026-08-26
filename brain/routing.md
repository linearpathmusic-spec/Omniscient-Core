---
title: Routing — How Queries Flow Through the Brain
---

# Routing — How Queries Flow Through the Brain

## Architecture Decision: BRN-001

Use a hybrid routing system.

The LLM performs semantic intent classification and identifies information needs.

A deterministic mapping converts information needs into one or more memory scopes.

The `brain_query` tool performs retrieval and enforces technical, security, and resource constraints.

Routing is multi-label rather than single-label.

A minimal working-state context is always loaded.

Retrieval escalates progressively: state → memory/wiki → raw evidence → external sources.

The tool may recommend additional memory scopes but should not hard-reject semantically ambiguous routing decisions.

All routing and retrieval operations are logged for future evaluation.

> Let the LLM decide meaning; let software enforce mechanics.

---

## The Three-Stage Router

```
             USER REQUEST
                   │
                   ▼
        ┌────────────────────┐
        │  1. INTENT ROUTER  │
        └─────────┬──────────┘
                  │
                  ▼
        classify information need
                  │
                  ▼
        ┌────────────────────┐
        │ 2. MEMORY PLANNER  │
        └─────────┬──────────┘
                  │
                  ▼
        choose memory scopes
         + search strategy
                  │
                  ▼
        ┌────────────────────┐
        │ 3. BRAIN QUERY     │
        └─────────┬──────────┘
                  │
                  ▼
              retrieval
                  │
                  ▼
          confidence check
             │          │
           enough      weak
             │          │
             ▼          ▼
           answer    expand search
```

### Stage 1: Intent Router (LLM)

Classify the request into information needs — not filenames.

```yaml
intent:
  primary: research
  secondary:
    - implementation

information_needs:
  - current_project_state
  - prior_decisions
  - technical_knowledge
  - procedure

temporal_scope:
  current: true
  historical: true

evidence_required: false
```

**Do not choose filenames yet.** Identify what kind of cognition is required.

### Stage 2: Memory Planner (Deterministic)

Map information needs to memory scopes via a lookup table.

```python
MEMORY_MAP = {
    "current_project_state": ["state"],
    "prior_events": ["episodic"],
    "prior_decisions": ["decisions"],
    "facts": ["semantic"],
    "concepts": ["semantic"],
    "procedure": ["procedural"],
    "project_context": ["project"],
    "capabilities": ["tools"],
    "evidence": ["raw"],
}
```

Example:

```
current_project_state
prior_decisions
technical_knowledge
procedure
```

becomes:

```
state
decisions
semantic
procedural
```

### Stage 3: brain_query (Tool)

```python
brain_query(
    query: str,
    memory_types: list[str],
    top_k: int = 8,
    project: str | None = None,
    time_range: str | None = None,
    entities: list[str] | None = None,
    confidence_threshold: float = 0.65
)
```

The tool accepts multiple memory types simultaneously. Real questions rarely fit into exactly one bucket.

---

## Always-Read Context

Not all memory should be routed. A small amount is automatically available on every meaningful turn.

**Always loaded:**

- `brain/constitution.md`
- `state/current.md`
- `state/context.md`

**Conditionally loaded:**

- `projects/<current>/overview.md` (if a project is active)

The router only decides whether to retrieve **additional** memories beyond this baseline.

This avoids needless retrieval calls for:

> "Rename this variable."

---

## Progressive Retrieval

Don't query every relevant memory immediately. Use retrieval stages.

| Pass | Scope | Cost |
| --- | --- | --- |
| 0 | constitution, state, active project | Always loaded |
| 1 | Top 3-5 results from likely memories | Cheap |
| 2 | Additional memory categories, synonyms, entities | Medium |
| 3 | Raw documents | Expensive |
| 4 | External research (web, APIs) | Most expensive |

If confidence is high at Pass 1, stop. Otherwise escalate.

```
cheap
 │
 ▼
state
 │
 ▼
wiki
 │
 ▼
memory
 │
 ▼
raw
 │
 ▼
external research
 │
 ▼
expensive
```

---

## Information-Need → Memory Mapping

| User needs | Memory |
| --- | --- |
| What are we doing right now? | `state` |
| What happened previously? | `episodic` |
| What decision did we make? | `decisions` |
| What is X? | `semantic/wiki` |
| How do I do X? | `procedural/skills` |
| What does this project contain? | `project` |
| What tools can I use? | `tool/system` |
| What evidence supports this? | `raw/evidence` |

---

## Routing Rules

| Memory | Query When the Request Involves |
| --- | --- |
| **state** | current goal, active task, next action, pending work, blockers, current project context |
| **episodic** | previously, earlier, yesterday, last time, what happened, what we tried, previous attempts |
| **decisions** | what did we decide, why did we choose, architecture choices, rejected alternatives, trade-offs |
| **semantic** | factual knowledge, definitions, concepts, entities, domain knowledge, relationships |
| **procedural** | how to, implement, configure, debug, research, perform a workflow |
| **project** | project architecture, repository structure, project requirements, project-specific conventions, implementation history |
| **tools** | what capabilities exist, which tool to invoke, tool parameters, API limitations, environment constraints |
| **raw/evidence** | citations needed, wiki claim disputed, confidence low, safety/financial/legal critical, verification requested |

---

## Retrieval Hierarchy

`raw` should normally not be first. The wiki is the compiled knowledge layer; raw is the evidence.

> "What is Kubernetes?" → query `wiki/concepts/kubernetes.md`
> "Where did we get this claim about Kubernetes?" → descend into `raw/`

---

## Confidence-Based Behavior

| Confidence | Action |
| --- | --- |
| >= 0.85 | Answer directly from brain |
| 0.65 - 0.84 | Answer with caveat, note uncertainty |
| 0.50 - 0.64 | Expand search (Pass 2) |
| < 0.50 | Escalate to raw evidence (Pass 3) |
| < 0.35 | External research (Pass 4) |

Uncertainty should increase retrieval breadth, not immediately cause the agent to ask the user.

---

## Soft Validation

`brain_query` validates:

- Memory type exists
- Access allowed
- Query valid
- Requested scope isn't absurd
- Result budget respected
- Sensitive memories obey policy

But does NOT hard-reject:

> "This semantic query should have been episodic."

Instead, use soft diagnostics:

```json
{
  "warning": "Query appears historical; consider episodic memory."
}
```

---

## Observability

All routing and retrieval operations are logged:

```yaml
timestamp: 2026-08-22T00:21
query: "Why did we choose the hybrid router?"
intent: architecture_recall

requested_memory:
  - decisions
  - semantic

retrieval:
  decisions:
    hits: 3
    best_score: 0.94
  semantic:
    hits: 2
    best_score: 0.77

selected:
  - memory/decisions/router.md

latency_ms: 42
coverage: 0.88
missing_information: []
```

Later these logs become the router evaluation dataset.

---

## Search Evolution

| Stage | Method | Wiki Size |
| --- | --- | --- |
| 1 | `index.md` + grep + ripgrep | < 100 sources |
| 2 | BM25 / full-text search | 100-500 sources |
| 3 | Hybrid: BM25 + embeddings + metadata | 500-2000 sources |
| 4 | Reranking + entity graph + temporal | 2000-10000 sources |
| 5 | Agentic retrieval (Pi decides search path) | 10000+ sources |

---

## Future: Auto Routing

Eventually support:

```python
brain_query(
    query="Why did we choose SQLite?",
    memory_types=["auto"]
)
```

The tool does: lightweight classifier + keyword heuristics + LLM route recommendation.

Don't build `auto` first. Get the explicit architecture working and observable first.

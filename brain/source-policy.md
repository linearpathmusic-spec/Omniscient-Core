---
title: Source Policy — How Raw Sources Are Managed
---

# Source Policy — How Raw Sources Are Managed

## Raw Sources Are Immutable

Once ingested, raw sources should not silently change. They are the ground truth.

## Ingestion Process (Phase 2)

1. **Receive source** — file path (URLs, web fetching are future adapters)
2. **Compute SHA-256** — hash of content for deduplication and content-derived IDs
3. **Derive source ID** — `SRC-{sha256[:12]}`
4. **Check for existing** — if same bytes, return existing (idempotent)
5. **Create source directory** — `raw/sources/SRC-{id}/`
6. **Atomic copy source** — `source.md` (no duplication for textual sources)
7. **Write metadata.yaml** — source_id, sha256, filename, ingested_at, project
8. **Log ingestion** — append to `logs/ingestion.jsonl`

## Source Directory Layout (Phase 2)

```text
raw/sources/
└── SRC-a91f72b310ce/
    ├── source.md          # Original content (atomic copy)
    └── metadata.yaml      # Source metadata
```

For Phase 2, textual sources are stored directly as `source.md`.
No duplication — the original bytes are the readable content.

Future PDF ingestion will legitimately produce:

```text
raw/sources/
└── SRC-xyz/
    ├── source.pdf         # Original binary
    ├── content.md         # Extracted text
    └── metadata.yaml
```

## Supported Source Types (Phase 2)

- `.md` — Markdown
- `.txt` — Plain text
- `.json` — JSON
- `.yaml` / `.yml` — YAML

## Source Metadata

```yaml
---
source_id: SRC-a91f72b310ce
sha256: <full 64-char hex hash>
filename: paper.md
ingested_at: 2026-08-22T10:10:00-04:00
project: super-brain
authority: primary | secondary | unknown
---
```

`authority` (Phase 6) is a categorical classification to support Pi's review
reasoning. Allowed values:

- `primary` — official standard, original research, government law
- `secondary` — review article, technical blog
- `unknown` — forum post, unattributed content (default)

Authority is metadata, not a numeric score. The runtime never enforces
source counts or reputation thresholds — Pi reasons about source quality
under `brain/review-policy.md`.

Phase 2 does not require (and Phase 6 still forbids):

- `authority_score`
- `trust_score`
- `confidence_score`
- `credibility_score`
- `freshness_score`
- `source_quality_score`

## Deduplication

- Compute SHA-256 on ingestion
- If hash matches existing source, return existing (idempotent)
- Identical bytes → identical SHA → identical source ID
- Logged as `operation: "existing"` in ingestion log

## Immutability Guarantee

Brain APIs guarantee:

- `brain_write` cannot write to `raw/`
- `brain_ingest` never overwrites existing source IDs
- Source files can be administratively repaired by the owner

## Rules

- Never modify raw source content via Brain APIs
- Never delete raw sources (archive instead)
- Always compute SHA-256 on ingestion
- Always create metadata.yaml
- Always log ingestion
- Link knowledge pages to source(s) via `source_refs` frontmatter
- One source can support many knowledge pages

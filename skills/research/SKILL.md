---
name: research
scope: research
status: active
version: 1
---

# Research Paper Ingestion

## Procedure

1. Receive a research paper (URL, PDF, or text)
2. Extract metadata: title, authors, date, abstract
3. Compute SHA-256 hash of the raw content
4. Store raw content in `raw/` with source ID
5. Compile key findings into a wiki page in `knowledge/concepts/`
6. Link wiki page to raw source via `source_refs`
7. Log ingestion in `logs/ingestion.md`

## Rules

- Raw sources are immutable
- Wiki claims must cite source IDs
- Unverified findings are labeled as such
- Duplicate content is deduplicated by hash

## Example

```text
Input: https://arxiv.org/abs/2301.xxxxx
Output:
  raw/src_20260821_001.md (raw content)
  knowledge/concepts/transformer-efficiency.md (compiled wiki)
```

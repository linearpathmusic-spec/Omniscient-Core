---
title: Knowledge Library Guide
kind: concept
status: provisional
---

# Your Knowledge Library

This repo ships with an **empty** library. The directories here are the
category tree — nothing is learned yet.

Build the library with the tools:

- `brain_ingest` — lock a source (paper, book, doc, repo) into `raw/sources/`
  as immutable evidence (SRC-* ids)
- `brain_write` — create source-backed knowledge pages under this tree
  (`knowledge/<category>/<topic>.md`)
- `brain_review` — verify / dispute pages against their evidence

Pages are plain markdown with frontmatter: `title`, `kind`, `source_refs`,
`status` (`provisional` | `verified` | `disputed`). See `brain/schema.md`.

Categories are just directories — rename, add, or drop them freely.

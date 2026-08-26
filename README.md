# Omniscient-Core

A persistent "brain" for an AI agent — small and stable on the outside,
honest about what it doesn't know on the inside.

## Where the idea came from

Andrej Karpathy put out a simple idea a while back: instead of constantly
re-writing code, an AI should build a *brain* — ingest the sources it cares
about (papers, books, docs, repos) into a `raw/` folder, let an LLM read
them and write back structured, interlinked notes, then keep feeding those
notes back in until the knowledge base grows on its own. Call it a "second
brain" or an "idea file" depending on the day.

The point was the shape of the thing, not the implementation. That's what we
liked about it, and it's the shape here — but we built the engine ourselves,
with one obsession: the system has to tell the difference between something
it actually has evidence for and something it's just confident about. Most
retrieval setups hide that behind a fake "trust score." This one doesn't.

## What it actually is

A set of six functions that an agent calls, backed by real modules:

| Function | Does |
| --- | --- |
| `brain_query` | Search the knowledge base, ranked, with provenance |
| `brain_ingest` | Lock a source into `raw/` as immutable evidence |
| `brain_write` | Create or update one knowledge page (with concurrency checks) |
| `brain_remember` | Record a decision or a lesson (append-only) |
| `brain_skill` | Propose / approve / reject a procedure — owner gates it |
| `brain_review` | Move a page between epistemic states |

Everything the agent retrieves goes through `brain_query`. That interface
hasn't changed since the first version, even though the search engine behind
it went from flat lexical scoring to Okapi BM25.

## The honest part — epistemic states

Every knowledge page is in one of three states, and the transitions are
enforced, not suggested:

- **provisional** — learned, not reviewed enough to lean on yet (the default)
- **verified** — the evidence was actually checked and supports it
- **disputed** — credible evidence conflicts with it

You can't fudge `provisional → verified` without a real source ref, and
`verified` pages are content-locked: to change verified knowledge you have to
write the correction (which drops it back to provisional) and review it again.
No silent self-modification, no confidence scores pretending to be certainty.

## How it's built

```
brain/          governance (constitution, policies) + the six runtime modules
knowledge/      the actual corpus — source-backed pages with provenance
raw/sources/    immutable evidence, keyed by SRC-* ids
raw/sources-raw/ curated pre-ingest source files
history/        decisions (DEC-*) and lessons (LES-*), append-only
skills/         owner-approved procedures
tools/          thin CLI wrappers around the six functions
tests/          self-contained tests — no corpus required
```

The design principle that shows up everywhere: **small API, deep modules.**
Each capability was added as one function and one module, and the surface the
agent sees never grew fat.

## Bring your own knowledge

The repo ships as a working program with an **empty** knowledge library:
`knowledge/` contains only the empty category tree, and `raw/` starts empty.
There is nothing to learn from out of the box — you build your own library
with the same loop the design intends: `brain_ingest` locks a source in as
immutable evidence, `brain_write` creates source-backed pages under
`knowledge/`, `brain_review` moves pages between epistemic states.

## Running it

```bash
pip install -e .            # needs Python 3.11+ and PyYAML

python3 -m pytest tests/ -q            # the mechanical tests
python3 tools/brain_lint.py            # corpus health (0 clean / 1 errors / 2 warnings)

python3 tools/brain_query.py   --query "what is RAG" --top-k 5
python3 tools/brain_ingest.py  --source path/to/paper.pdf --authority primary
python3 tools/brain_review.py  verify --path knowledge/ai/rag.md --expected-sha <sha> --evidence SRC-...
```

Tests run in isolated temp roots, so they never touch your live corpus.

## Security

- **Write boundary** — `brain_write` and `brain_review` only ever touch
  `knowledge/**/*.md`. Path traversal, absolute paths, and escaping symlinks
  are rejected (tested). Constitution, policies, raw evidence, history, and
  skills are not writable through the Brain API.
- **SSRF-guarded URL ingestion** — `brain_ingest` of an http(s) URL only
  fetches public hosts: the hostname is resolved and refused if it lands in
  private / loopback / link-local / reserved ranges, redirects are not
  followed, and non-http schemes (`file://`, `gopher://`, …) are rejected.
- **No silent self-modification** — verified pages are content-locked;
  changing them requires dispute → rewrite → re-review. Skills are
  Markdown-only and owner-approved. Logs record hashes and paths, never
  document content.

## License

MIT — see [LICENSE](LICENSE).

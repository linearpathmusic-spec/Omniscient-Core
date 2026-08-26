"""
Super Brain — Lexical Retrieval Engine (BM25)

Deep module: Pi sees BrainSearch.query(). Pi does not see the machinery.

Phase 7: BM25 core — IDF weighting, length normalization, field-weighted
term scoring — layered with the Phase 1 metadata boosts (title phrase,
alias, tag, filename, project). Public interface unchanged:

    brain_query(query, top_k, project) -> QueryResponse

Measured justification (Phase 1 baseline vs Phase 7):
    - Hit@1: 0.70 -> improved (evals/retrieval/run_eval.py)
    - Failures fixed were exactly the no-IDF / no-length-normalization /
      tokenization classes (see wiki/phase-7-done.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("brain.search")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_ROOT = Path(__file__).resolve().parent.parent.parent

SEARCH_DIRS = ["knowledge", "history", "skills", "tools", "brain"]

ALLOWED_EXTENSIONS = {".md", ".txt", ".yaml", ".yml", ".json"}

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB

STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above",
    "below", "between", "out", "off", "over", "under", "again",
    "further", "then", "once", "here", "there", "when", "where", "why",
    "how", "about", "within", "without", "onto", "upon", "among", "across",
    "all", "both", "each", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "just", "because", "but", "and", "or",
    "we", "i", "my", "me", "this", "that", "these", "those", "it",
    "its", "what", "which", "who", "whom", "whose",
})

# BM25 parameters (Okapi BM25)
BM25_K1 = 1.5  # term-frequency saturation
BM25_B = 0.75  # length normalization strength

# Field weights for BM25 term scoring (title >> heading > body)
W_FIELD_TITLE = 4.0
W_FIELD_HEADING = 2.0
W_FIELD_BODY = 1.0

# Metadata boosts (additive, preserved from Phase 1)
W_TITLE_PHRASE = 12  # exact phrase match in title
W_FILENAME = 6       # filename contains query token
W_ALIAS = 6          # alias match
W_TAG = 4            # tag match
W_PROJECT = 5        # project scope match

# Old Phase 1 raw-TF weights removed in Phase 7 (replaced by BM25):
#   W_TITLE_TOKEN, W_HEADING, W_BODY_PHRASE, W_BODY_TOKEN, MAX_BODY_TERM_SCORE


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Document:
    """Parsed document with metadata and content."""
    path: Path
    title: str = ""
    kind: str = ""
    project: str = ""
    date: str = ""
    status: str = ""
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    body: str = ""
    filename: str = ""
    score: int = 0


@dataclass
class SearchResult:
    """A single ranked result."""
    id: str = ""
    title: str = ""
    kind: str = ""
    project: str = ""
    status: str = ""
    path: str = ""
    score: float = 0.0
    snippet: str = ""
    source_refs: list[str] = field(default_factory=list)


@dataclass
class QueryResponse:
    """Full response from brain_query."""
    query_id: str = ""
    query: str = ""
    results: list[SearchResult] = field(default_factory=list)
    result_count: int = 0
    warning: str = ""


# ---------------------------------------------------------------------------
# Query normalization
# ---------------------------------------------------------------------------

def normalize_query(query: str) -> str:
    """Lowercase, collapse whitespace."""
    return " ".join(query.lower().split())


def _stem(word: str) -> str:
    """Light suffix normalizer (Porter step-1 subset).

    Recovers the morphological matching the Phase 1 substring scorer gave
    for free (learned~learn, caching~cache, types~type) while staying
    word-boundary safe. Applied identically to queries and documents.
    """
    if len(word) <= 4:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("ing") and len(word) > 5:
        return word[:-3]
    if word.endswith("ed") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is", "as")):
        return word[:-1]
    return word


def tokenize(query: str) -> list[str]:
    """Extract meaningful tokens from a normalized query.

    Phase 7: tokens of length < 2 are dropped (removes the stray 's' from
    possessives like "Karpathy's"), and tokens are light-stemmed.
    """
    normalized = normalize_query(query)
    tokens = re.findall(r"[a-z0-9]+", normalized)
    return [_stem(t) for t in tokens if t not in STOP_WORDS and len(t) >= 2]


# ---------------------------------------------------------------------------
# YAML frontmatter parser (no dependency beyond stdlib)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """
    Extract YAML frontmatter and body from text.
    Returns (metadata_dict, body_text).
    """
    metadata: dict[str, Any] = {}
    body = text

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1].strip()
            body = parts[2].lstrip("\n")
            # Minimal YAML parsing
            lines = fm_text.split("\n")
            i = 0
            while i < len(lines):
                line = lines[i]
                stripped = line.strip()
                i += 1

                if not stripped or stripped.startswith("#"):
                    continue

                if ":" not in stripped:
                    continue

                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()

                if not key:
                    continue

                # Handle inline list [a, b, c]
                if value.startswith("[") and value.endswith("]"):
                    inner = value[1:-1]
                    metadata[key] = [
                        v.strip().strip("'").strip('"')
                        for v in inner.split(",")
                        if v.strip()
                    ]
                    continue

                # Handle inline list item (- item on same line as key)
                if value.startswith("- "):
                    items = [value[2:].strip()]
                    # Collect continuation list items
                    while i < len(lines):
                        next_line = lines[i].strip()
                        if next_line.startswith("- "):
                            items.append(next_line[2:].strip())
                            i += 1
                        else:
                            break
                    metadata[key] = items
                    continue

                # Handle key with no value (multi-line list follows)
                if not value:
                    items = []
                    while i < len(lines):
                        next_line = lines[i]
                        next_stripped = next_line.strip()
                        if next_stripped.startswith("- "):
                            items.append(next_stripped[2:].strip())
                            i += 1
                        elif next_stripped and not next_stripped.startswith("#"):
                            break
                        else:
                            i += 1
                    if items:
                        metadata[key] = items
                    continue

                # Plain scalar value
                metadata[key] = value.strip("'").strip('"')

    return metadata, body


# ---------------------------------------------------------------------------
# Document parsing
# ---------------------------------------------------------------------------

def extract_headings(body: str) -> list[str]:
    """Extract markdown headings from body text."""
    headings = []
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            # Remove heading markers
            heading = re.sub(r"^#+\s*", "", stripped)
            headings.append(heading)
    return headings


def parse_document(path: Path) -> Document | None:
    """Parse a single document file into a Document object."""
    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            logger.debug("Skipping %s: exceeds max file size", path)
            return None

        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Cannot read %s: %s", path, exc)
        return None

    metadata, body = parse_frontmatter(text)

    doc = Document(
        path=path,
        title=metadata.get("title") or metadata.get("name") or path.stem,
        kind=metadata.get("kind", ""),
        project=metadata.get("project", ""),
        date=metadata.get("date", ""),
        tags=metadata.get("tags", []) or [],
        aliases=metadata.get("aliases", []) or [],
        source_refs=metadata.get("source_refs", []) or [],        filename=path.name,
    )

    doc.headings = extract_headings(body)
    doc.body = body
    doc.status = metadata.get("status", "")

    return doc


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def discover_documents(root: Path | None = None) -> list[Path]:
    """Discover all searchable files under SEARCH_DIRS."""
    base = root or BRAIN_ROOT
    files: list[Path] = []

    for search_dir in SEARCH_DIRS:
        dir_path = base / search_dir
        if not dir_path.is_dir():
            continue
        for ext in ALLOWED_EXTENSIONS:
            for path in dir_path.rglob(f"*{ext}"):
                if path.is_file() and not path.is_symlink():
                    files.append(path)
                elif path.is_symlink():
                    # Check if symlink resolves within BRAIN_ROOT
                    try:
                        resolved = path.resolve()
                        if resolved.is_relative_to(base):
                            files.append(path)
                    except OSError:
                        pass

    return sorted(files)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _exact_phrase_match(text: str, query: str) -> bool:
    """Check if normalized text contains the full query phrase."""
    return query.lower() in text.lower()


# ---------------------------------------------------------------------------
# BM25 scoring
# ---------------------------------------------------------------------------


def _bm25_idf(df: int, n_docs: int) -> float:
    """Okapi IDF: rare terms carry more weight; df==n_docs still > 0."""
    return math.log(1 + (n_docs - df + 0.5) / (df + 0.5))


def _bm25_tf(tf: int, dl: int, avgdl: float, k1: float = BM25_K1, b: float = BM25_B) -> float:
    """Saturated, length-normalized term frequency."""
    if dl <= 0:
        return 0.0
    denom = tf + k1 * (1 - b + b * (dl / max(avgdl, 1.0)))
    return (tf * (k1 + 1)) / denom


def _doc_stats(documents: list["Document"], terms: list[str]) -> dict[str, Any]:
    """Corpus stats needed for BM25: n_docs, per-term doc frequency, avgdl."""
    n_docs = len(documents)
    df = {t: 0 for t in terms}
    total_tokens = 0
    for doc in documents:
        title_toks = tokenize(doc.title)
        heading_toks = tokenize(" ".join(doc.headings))
        body_toks = tokenize(doc.body)
        filename_toks = tokenize(doc.filename)
        for t in terms:
            if t in title_toks or t in heading_toks or t in body_toks or t in filename_toks:
                df[t] += 1
        total_tokens += (len(title_toks) + len(heading_toks) + len(body_toks)
                         + len(filename_toks))
    avgdl = total_tokens / max(n_docs, 1)
    return {"n_docs": n_docs, "df": df, "avgdl": avgdl}


def _field_bm25(field_tokens: list[str], terms: list[str], dl: int, avgdl: float,
                df: dict[str, int], n_docs: int, weight: float) -> float:
    """Sum of saturated+normalized BM25 term scores over one field."""
    counts = {t: 0 for t in terms}
    for tok in field_tokens:
        if tok in counts:
            counts[tok] += 1
    score = 0.0
    for t in terms:
        tf = counts[t]
        if tf == 0:
            continue
        idf = _bm25_idf(df.get(t, 1), n_docs)
        score += _bm25_tf(tf, dl, avgdl) * idf * weight
    return score


def score_document(doc: "Document", terms: list[str], query: str,
                   project: str | None, stats: dict[str, Any] | None = None) -> float:
    """
    Score a document against query terms.

    BM25 core (IDF + length normalization + field weights) plus additive
    metadata boosts. Returns float; higher = more relevant.

    stats (optional): {"n_docs": int, "df": {term: count}, "avgdl": float}
    computed by BrainSearch.query() over the corpus. When omitted (unit
    tests), degrades to single-document scoring with maximal IDF.
    """
    if stats is None:
        n_docs = 1
        df = {t: 1 for t in terms}
        avgdl = len(tokenize(doc.title)) + len(tokenize(" ".join(doc.headings))) + len(tokenize(doc.body))
    else:
        n_docs = stats["n_docs"]
        df = stats["df"]
        avgdl = stats["avgdl"]

    title_tokens = tokenize(doc.title)
    heading_tokens = tokenize(" ".join(doc.headings))
    body_tokens = tokenize(doc.body)
    dl = len(title_tokens) + len(heading_tokens) + len(body_tokens)

    score = 0.0

    # BM25 core
    score += _field_bm25(title_tokens, terms, dl, avgdl, df, n_docs, W_FIELD_TITLE)
    score += _field_bm25(heading_tokens, terms, dl, avgdl, df, n_docs, W_FIELD_HEADING)
    score += _field_bm25(body_tokens, terms, dl, avgdl, df, n_docs, W_FIELD_BODY)

    # Metadata boosts (additive)
    title_lower = doc.title.lower()
    filename_lower = doc.filename.lower()

    if query.lower() in title_lower:
        score += W_TITLE_PHRASE

    # Metadata boosts, scaled by IDF. A flat boost lets a near-universal
    # term like the project name "brain" (df ~= n_docs) out-weight a rare
    # term that lands in a filename or title ("constitution", "schema").
    # Scaling by IDF keeps boosts in the same currency as BM25 terms.
    for term in terms:
        idf = _bm25_idf(df.get(term, 1), n_docs)
        if term in filename_lower:
            score += W_FILENAME * idf

    # Alias/tag boosts: one boost PER QUERY TERM (not per alias/tag entry)
    # and IDF-scaled. The old loops broke only the inner loop, so N aliases
    # each containing a term added N x W_ALIAS — a doc with 2 aliases
    # mentioning 'brain' outscored its entire BM25 contribution.
    for term in terms:
        idf = _bm25_idf(df.get(term, 1), n_docs)
        if any(term in alias.lower() or query.lower() in alias.lower()
               for alias in doc.aliases):
            score += W_ALIAS * idf
            break

    for term in terms:
        idf = _bm25_idf(df.get(term, 1), n_docs)
        if any(term in tag.lower() for tag in doc.tags):
            score += W_TAG * idf
            break

    if project and doc.project:
        if project.lower() == doc.project.lower():
            score += W_PROJECT

    return score


# ---------------------------------------------------------------------------
# Snippet extraction
# ---------------------------------------------------------------------------

def extract_snippet(doc: Document, terms: list[str], query: str,
                    max_chars: int = 600) -> str:
    """
    Extract the best matching region from document body.
    Returns 300-800 characters around the strongest match.
    """
    body = doc.body
    if not body:
        return ""

    # Find best match position
    best_pos = -1
    best_type = 0  # 0=none, 1=phrase, 2=term

    query_lower = query.lower()
    if query_lower in body.lower():
        best_pos = body.lower().index(query_lower)
        best_type = 1
    else:
        for term in terms:
            pos = body.lower().find(term)
            if pos >= 0:
                if best_type < 2:
                    best_pos = pos
                    best_type = 2
                break

    if best_pos < 0:
        # No match found, return first paragraph
        first_para = body.split("\n\n")[0][:max_chars]
        return first_para.strip()

    # Extract context window around match
    window_start = max(0, best_pos - 200)
    window_end = min(len(body), best_pos + len(query) + 200)

    snippet = body[window_start:window_end]

    # Clean up: don't start mid-sentence if possible
    if window_start > 0:
        # Find nearest sentence boundary
        for delim in [".\n", "!\n", "?\n", "\n\n"]:
            idx = snippet.find(delim)
            if idx > 50:  # Don't trim too aggressively
                snippet = snippet[idx + len(delim):]
                break

    # Truncate if needed
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars - 3] + "..."

    return snippet.strip()


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def validate_path(path: Path, root: Path | None = None) -> bool:
    """Ensure path is within BRAIN_ROOT and has allowed extension."""
    base = root or BRAIN_ROOT
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(base):
            return False
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            return False
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Query ID generation
# ---------------------------------------------------------------------------

def generate_query_id() -> str:
    """Generate a unique query ID."""
    ts = time.time()
    rand = hashlib.sha256(str(ts).encode()).hexdigest()[:6]
    return f"bq_{rand}"


# ---------------------------------------------------------------------------
# Main query engine
# ---------------------------------------------------------------------------

class BrainSearch:
    """
    Phase 1 lexical retrieval engine.

    Public API: query(query, top_k, project) -> QueryResponse
    """

    def __init__(self, root: Path | None = None):
        self.root = root or BRAIN_ROOT

    def query(self, query: str, top_k: int = 5,
              project: str | None = None) -> QueryResponse:
        """
        Execute a retrieval query.

        Args:
            query: Natural-language search query.
            top_k: Maximum number of results to return.
            project: Optional project scope filter/boost.

        Returns:
            QueryResponse with ranked results.
        """
        try:
            start_time = time.monotonic()
        except (OSError, ValueError):
            start_time = time.time()

        if not query or not query.strip():
            return QueryResponse(
                query_id=generate_query_id(),
                query=query,
                results=[],
                result_count=0,
                warning="Empty query.",
            )

        # Normalize and tokenize
        normalized = normalize_query(query)
        terms = tokenize(query)

        if not terms:
            return QueryResponse(
                query_id=generate_query_id(),
                query=query,
                results=[],
                result_count=0,
                warning="Query contains only stop words.",
            )

        # Discover and parse documents
        doc_paths = discover_documents(self.root)
        documents: list[Document] = []

        for path in doc_paths:
            if not validate_path(path, self.root):
                continue
            doc = parse_document(path)
            if doc is not None:
                documents.append(doc)

        # Corpus stats for BM25 (IDF + length normalization)
        stats = _doc_stats(documents, terms)

        # Score and rank
        candidates: list[tuple[Document, float]] = []
        for doc in documents:
            s = score_document(doc, terms, normalized, project, stats)
            if s > 0:
                candidates.append((doc, s))

        candidates.sort(key=lambda x: x[1], reverse=True)

        # Build results
        results: list[SearchResult] = []
        for doc, score in candidates[:top_k]:
            snippet = extract_snippet(doc, terms, normalized)
            result = SearchResult(
                id=doc.title,  # Will be set from frontmatter id if available
                title=doc.title,
                kind=doc.kind,
                project=doc.project,
                status=doc.status,
                path=str(doc.path.relative_to(self.root)),
                score=score,
                snippet=snippet,
                source_refs=doc.source_refs,  # real provenance, not aliases
            )
            results.append(result)

        try:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
        except (OSError, ValueError):
            elapsed_ms = 0

        response = QueryResponse(
            query_id=generate_query_id(),
            query=query,
            results=results,
            result_count=len(results),
        )

        if not results and project:
            # Check if project directory exists
            proj_path = self.root / "knowledge" / "projects" / project
            if not proj_path.is_dir():
                response.warning = (
                    "Requested project scope was not found; "
                    "global search used."
                )

        # Log with the same ID returned to the caller for end-to-end tracing.
        _log_retrieval(query, project, results, elapsed_ms, self.root, response.query_id)

        return response


# ---------------------------------------------------------------------------
# Retrieval logging
# ---------------------------------------------------------------------------

def _log_retrieval(query: str, project: str | None,
                   results: list[SearchResult], latency_ms: int,
                   root: Path | None = None,
                   query_id: str | None = None) -> None:
    """Append a retrieval record to logs/retrieval.jsonl.

    Logs under the search instance's root so temp-root evals don't
    pollute the real brain's logs.
    """
    log_dir = (root or BRAIN_ROOT) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "retrieval.jsonl"

    record = {
        "timestamp": _now_iso(),
        "query_id": query_id or generate_query_id(),
        "query": query,
        "project": project,
        "results": [
            {"path": r.path, "score": r.score} for r in results
        ],
        "latency_ms": latency_ms,
    }

    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.warning("Failed to write retrieval log: %s", exc)


def _now_iso() -> str:
    """Return current timestamp in ISO 8601 format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

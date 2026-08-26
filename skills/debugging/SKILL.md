---
name: debugging
scope: debugging
status: active
version: 1
---

# Debugging Procedure

## Procedure

1. Identify the symptom (wrong answer, missing result, crash)
2. Check retrieval logs in `logs/retrieval.jsonl`
3. Verify the query terms match expected documents
4. Check if the document has proper frontmatter
5. Run eval cases to reproduce the failure
6. If retrieval is correct but answer is wrong, check reasoning
7. If retrieval is wrong, check scoring weights

## Retrieval Debug Checklist

- [ ] Query terms are meaningful (not stop words)
- [ ] Document has valid frontmatter
- [ ] Document is in a searchable directory
- [ ] Score reflects expected relevance ordering
- [ ] Snippet contains the matching context
- [ ] No path traversal vulnerabilities

## Example

Query: "Why did we choose hybrid routing?"
Expected: BRN-001 should rank highest
Check: title match (+12), tag match (+4), body match (+4) = 20+

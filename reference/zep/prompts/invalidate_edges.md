# Edge Invalidation (Contradiction Handling)

LLM determines which existing edges should be invalidated when new edges contradict them.

**Source:** `graphiti_core/prompts/invalidate_edges.py`

## Output schema

```json
{
  "contradicted_facts": [0, 1]
}
```

- List of edge IDs to invalidate.
- Empty list if no contradictions.

## Context

- Previous episodes
- Current episode
- Existing edges (sorted by timestamp)
- New edges

## Rules

- Only invalidate when there is clear evidence the relationship is no longer true.
- Do not invalidate merely because a fact wasn't mentioned.

## Implementation

- resolve_extracted_edges calls resolve_extracted_edge per edge.
- LLM returns duplicate_facts, contradicted_facts, fact_type.
- Contradicted edges get invalid_at set.

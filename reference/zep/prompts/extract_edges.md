# Edge / Fact Extraction

LLM extracts factual relationships between entities.

**Source:** `graphiti_core/prompts/extract_edges.py`

## Output schema

```json
{
  "edges": [
    {
      "relation_type": "LOVES",
      "source_entity_id": 0,
      "target_entity_id": 1,
      "fact": "Kendra loves Adidas shoes",
      "valid_at": "2025-03-15T10:00:00.000000Z",
      "invalid_at": null
    }
  ]
}
```

- relation_type: SCREAMING_SNAKE_CASE (e.g., WORKS_AT, FOUNDED).
- source_entity_id, target_entity_id: Index into ENTITIES list.
- fact: Natural language description.
- valid_at, invalid_at: ISO 8601; used for temporal queries.

## Context

- FACT TYPES (edge_types)
- PREVIOUS_MESSAGES
- CURRENT_MESSAGE
- ENTITIES (extracted nodes)
- REFERENCE_TIME (for resolving relative time)

## Reflexion

- Reflexion prompt checks for missing facts.

## Reuse advice

- Custom edge types via edge_types and edge_type_map.
- reference_time critical for "last week", "yesterday" etc.

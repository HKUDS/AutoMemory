# Entity Node Extraction

LLM extracts entities from episode content (message, text, or JSON).

**Source:** `graphiti_core/prompts/extract_nodes.py`

## Prompt variants

- **extract_message**: For conversational messages; extracts speaker and significant entities.
- **extract_text**: For plain text.
- **extract_json**: For JSON data.

## Output schema

```json
{
  "extracted_entities": [
    {"name": "EntityName", "entity_type_id": 0}
  ]
}
```

- entity_type_id maps to entity_types (e.g., Person, Organization, etc.).
- Custom entity types via entity_types dict (Pydantic models).

## Reflexion

- After extraction, reflexion prompt checks for missed entities.
- If missed entities found, re-extract with updated prompt (max 3 iterations).

## Reuse advice

- Provide entity_types as context; exclude via excluded_entity_types.
- Use previous_episodes for context (RELEVANT_SCHEMA_LIMIT).

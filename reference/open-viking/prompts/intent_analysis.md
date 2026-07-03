# Intent Analysis Prompt

**Purpose:** Analyze session context to generate typed query plans for retrieval across skill/resource/memory types.

**Source:** `openviking/prompts/templates/retrieval/intent_analysis.yaml` (v2.0.0)

## System Prompt

You are a context retrieval planner.

## User Prompt

**Variables:** `compression_summary`, `recent_messages`, `current_message`, `context_type`, `target_abstract`

**Analysis steps:**
1. Identify task type: operational / informational / conversational.
2. Check existing context coverage (summary + recent messages).
3. Generate prioritized queries (priority 1–5) only for genuinely missing information.

**Query style by context_type:**

| context_type | Query style | Example |
|-------------|-------------|---------|
| skill | Verb-led, action-oriented | "Create RFC document" |
| resource | Noun phrase, knowledge-oriented | "RFC document standard template" |
| memory | "User XXX" for user memory; "Experience executing XXX" for agent memory | "User preferences for API timeouts" |

**Output format:**
```json
{
  "reasoning": "The user needs... Current context covers... Missing: ...",
  "queries": [
    {
      "query": "User preferences for translate API configuration",
      "context_type": "memory",
      "intent": "Retrieve user's known preferences for this tool",
      "priority": 4
    }
  ]
}
```

**Constraints:**
- 0–5 queries (0 if existing context is sufficient).
- Each query has exactly one context_type.
- Priority 1–5 (5 = most critical).

## Notes

- **Constraints**: Must return valid JSON. Queries must be specific and non-redundant. Zero queries is valid when context is sufficient.
- **Typed queries**: The skill/resource/memory distinction enables routing to different retrieval paths (different root URIs, different scoring).
- **Priority**: Used by HierarchicalRetriever to order retrieval; higher-priority queries are searched first.
- **Reuse advice**: The typed query plan pattern is useful for any system with heterogeneous memory types. The "analyze coverage → generate only missing" logic avoids unnecessary retrieval.

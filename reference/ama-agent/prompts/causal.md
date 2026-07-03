# Causal Graph Extraction Prompt

**Purpose:** Extract causal relationships between events and state changes from a trajectory chunk. Used when `causal=True` in config.

**Template:** `CAUSAL_PROMPT_TEMPLATE`

**Variables:**
- `task`: Task description
- `trajectory_text`: Trajectory chunk text
- `previous_state_text`: Previous accumulated state (empty for first chunk)

**Output format:** LLM outputs two sections:
1. **CAUSAL_GRAPH** — JSON array of causal relationships
2. **STATE_MEMORY** — State memory content (same as compress prompt)

**Causal graph schema:**

```json
[
  {
    "cause": "description of triggering action/event",
    "effect": "description of resulting state change",
    "cause_turn": <turn number>,
    "effect_turn": <turn number>,
    "entities": ["entity1", "entity2"]
  }...
]
```

**Extraction:** `_extract_causal_graph_from_response` finds `**CAUSAL_GRAPH**` marker, extracts JSON array with regex.

**Source:** `src/method/ama_agent_core/prompt.py` — `CAUSAL_PROMPT_TEMPLATE`

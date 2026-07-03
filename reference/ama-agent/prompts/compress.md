# Compress Prompt (State Memory Extraction)

**Purpose:** Extract structured state information from a trajectory chunk. Used in `construct_state_memory` for each chunk.

**Template:** `COMPRESS_PROMPT_TEMPLATE`

**Variables:**
- `task`: Task description
- `trajectory_text`: Trajectory chunk text (formatted turns)
- `previous_state_text`: Previous accumulated state (empty for first chunk)

**Output format:** LLM outputs content after the marker `**STATE_MEMORY**`. The content can be JSON, structured text, or bullet points.

**Example format suggested in prompt:**
```json
{
  "objects": ["obj_1", "obj_2", "obj_3"],
  "obj_state": {
    "obj_1": [
      {"t": 1, "action":"", "state": "exact description"},
      {"t": 5, "action":"", "state": "exact description"}
    ]
  }
}
```

**Key requirements:**
1. Identify ALL relevant objects/entities/locations
2. Track state changes at specific turns with EXACT details
3. Include precise values, locations, concrete actions
4. Record exact error messages or feedback when relevant
5. List events chronologically for each object

**Source:** `src/method/ama_agent_core/prompt.py` — `COMPRESS_PROMPT_TEMPLATE`

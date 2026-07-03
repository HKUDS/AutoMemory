# Relevance Scoring Prompt (retrieve_with_qwen)

**Purpose:** Score each trajectory chunk’s relevance to a query on a scale of 0–10. Used in `retrieve_with_qwen` for Stage 2 retrieval.

**Template:** Inline in `retrieve_with_qwen` (utils.py)

**Variables:**
- `query`: User question
- `chunk_text`: Formatted chunk (e.g., Turn 0: Action=..., Observation=...; Turn 1: ...)

**Output format:** LLM responds with ONLY a single number from 0 to 10.

**Scoring guide:**
- 0 = completely irrelevant
- 5 = somewhat relevant
- 10 = highly relevant

**Considerations in prompt:**
- Does this chunk contain information directly answering the query?
- Are there relevant actions, observations, or state changes?
- How closely do the events relate to what the query is asking?

**Parsing:** Regex `r'(\d+(?:\.\d+)?)'` extracts first number; clamped to [0, 10].

**Chunking:** Trajectory grouped into chunks of 5 turns; each chunk scored; top 5 chunks selected; turn indices from top chunks merged (deduplicated, sorted).

**Source:** `src/method/ama_agent_core/utils.py` — `retrieve_with_qwen`

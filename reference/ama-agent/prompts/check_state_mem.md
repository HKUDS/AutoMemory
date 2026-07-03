# Check State Memory Sufficiency

**Purpose:** Determine if the compressed state memory contains enough information to answer a question. Used in Stage 1 of `memory_retrieve`.

**Template:** `CHECK_STATE_MEM_PROMPT_TEMPLATE`

**Variables:**
- `state_mem_str`: Compressed state memory string
- `query`: User question

**Output format:** LLM responds with ONLY "SUFFICIENT" or "NEED_RETRIEVAL" followed by a brief reason.

**Examples:**
- `SUFFICIENT: [reason why state memory is enough]`
- `NEED_RETRIEVAL: [what specific information is missing and needs to be retrieved]`

**Considerations in prompt:**
1. Does the state memory mention the relevant objects/entities in the question?
2. Does it contain the specific information needed (states, relationships, actions)?
3. Is the information detailed enough or just vague references?

**Parsing:** Code checks if `"NEED_RETRIEVAL"` appears in the response (case-insensitive); if so, proceeds to Stage 2 retrieval.

**Source:** `src/method/ama_agent_core/prompt.py` — `CHECK_STATE_MEM_PROMPT_TEMPLATE`

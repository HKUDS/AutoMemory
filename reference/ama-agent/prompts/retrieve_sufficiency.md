# Judge Sufficiency of Retrieved Chunks

**Purpose:** After retrieving top-k trajectory chunks, determine if the retrieved information is sufficient to answer the question. Used in Stage 2 of `memory_retrieve`.

**Template:** Inline in `_judge_sufficiency_and_build_context` (retrieve.py)

**Variables:**
- `question`: User question
- `state_mem`: State memory string
- `chunks_text`: Formatted retrieved chunks (Turn N: Action: ... Observation: ...)
- `len(chunks)`: Number of retrieved chunks

**Output format:** LLM responds with:
- `SUFFICIENT: [brief explanation]` — if can answer with this information
- `INSUFFICIENT: [what's missing]` — if need more information

**Parsing:** Code checks if `"SUFFICIENT"` appears and `"INSUFFICIENT"` does not (case-insensitive).

**Analysis questions in prompt:**
1. Does the retrieved information directly answer the question?
2. Are there any missing details needed to provide a complete answer?
3. Is the information clear and unambiguous?

**Final context:** Includes state memory + task + retrieved chunks + sufficiency assessment (LLM response).

**Source:** `src/method/ama_agent_core/retrieve.py` — `_judge_sufficiency_and_build_context`

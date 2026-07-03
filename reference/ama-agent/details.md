# Details for ama-agent

Implementation-oriented reference for the AMA-Agent memory system design.

---

## 1. System architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  memory_construction(traj_text, task)                            │
│  Parse → Chunk → Compress per chunk (LLM) → Accumulate state     │
│  Optional: causal graph, turn embeddings                         │
├─────────────────────────────────────────────────────────────────┤
│  Memory: state_mem, text_mem, embed_mem, trajectory, causal_graph│
├─────────────────────────────────────────────────────────────────┤
│  memory_retrieve(memory, question)                               │
│  Stage 1: LLM SUFFICIENT vs NEED_RETRIEVAL for state_mem         │
│  Stage 2: retrieve_with_qwen → top 5 chunks → LLM judge sufficiency│
│  Return: state_mem + task + (optional) retrieved chunks + assessment│
└─────────────────────────────────────────────────────────────────┘
```

**Components:**
- **construct_state_memory**: `_parse_trajectory_text` → `_process_trajectory` (or `_process_trajectory_causal`) → `_build_turn_embeddings` (optional).
- **memory_retrieve**: `CHECK_STATE_MEM_PROMPT_TEMPLATE` → `retrieve_with_qwen` → `_extract_chunks` → `_judge_sufficiency_and_build_context`.
- **retrieve_with_qwen**: Groups trajectory into chunks of 5 turns; scores each chunk 0–10 with LLM; returns top 5 chunks’ turn indices. Also available: `retrieve_with_llm` (keyword extraction + traj_find), `retrieve_with_embed` (cosine similarity on turn embeddings).

---

## 2. Data model and schema

**state_mem:**
- Type: `str` (or None)
- Content: Compressed state memory string; extracted from LLM response after `**STATE_MEMORY**` marker.
- Update semantics: Replace (each chunk overwrites accumulated state).

**text_mem:**
- Type: `Dict`
- Fields: `task`, `trajectory_text`, `trajectory_data` (trajectory, task, episode_id), `episode_id`, `num_turns`.
- Update semantics: Immutable after construction.

**embed_mem:**
- Type: `Dict` or None
- Fields: `embeddings`, `turn_texts`, `turn_indices`.
- Update semantics: Built once if embed_engine provided.

**trajectory:**
- Type: `List[Dict]`
- Each turn: `turn_idx`, `action`, `observation`.
- Update semantics: Immutable after construction.

**causal_graph:**
- Type: `List[Dict]` or None
- Each edge: `cause`, `effect`, `cause_turn`, `effect_turn`, `entities`.
- Update semantics: Append (extend) across chunks when causal=True.

---

## 3. Storage and indexing

- **Storage backend**: In-memory (Python dict); no persistent DB.
- **Indexing**: No explicit index; retrieval uses LLM relevance scoring over trajectory chunks.
- **Retrieval units**: Chunk = 5 turns (in retrieve_with_qwen); chunk = turn-based by chunk_size (in construction).
- **Hybrid**: Optional turn embeddings for `retrieve_with_embed`; not used in default pipeline.

---

## 4. Retrieval logic

1. **Candidate generation**: 
   - Stage 1: State memory only (no candidates).
   - Stage 2: If NEED_RETRIEVAL, `retrieve_with_qwen` groups trajectory into chunks of 5 turns; scores each chunk 0–10 with LLM; returns top 5 chunks’ turn indices.
2. **Filtering**: None; top 5 chunks by score.
3. **Ranking**: LLM relevance score 0–10.
4. **Formatting**: Format chunks as `Turn N: Action: ... Observation: ...`; append to context with state memory, task, and sufficiency assessment.

---

## 5. Store and update logic

- **Store trigger**: Single call to `memory_construction(traj_text, task)`.
- **Extraction**: Parse trajectory text (Turn N: Action: / Observation:); chunk by `chunk_size`; LLM compress per chunk.
- **Deduplication**: None; state replaces per chunk.
- **Slot update vs new item**: State memory is a single slot; each chunk overwrites (replace).
- **Validation**: Extract content after `**STATE_MEMORY**`; if missing, chunk_state is None and accumulated state is not updated.

---

## 6. Consolidation and lifecycle

- **Consolidation**: None; construction is one-shot.
- **TTL / compaction / eviction**: None; memory lives in process.
- **Stale**: Not applicable; trajectory is static.
- **Conflict**: None; state is replaced per chunk.

---

## 7. Important implementation details

**Key functions:**
- `construct_state_memory` (construct.py): `_parse_trajectory_text`, `_process_trajectory`, `_process_trajectory_causal`, `_build_turn_embeddings`.
- `extract_state_memory_from_response` (utils.py): Finds `**STATE_MEMORY**` marker, extracts content after it.
- `retrieve_with_qwen` (utils.py): Chunks of 5 turns; relevance prompt; parse score 0–10; top 5 chunks.
- `memory_retrieve` (retrieve.py): Two-stage retrieval.

**Chunking:**
- Construction: By character count (`chunk_size`); each turn’s text length contributes; turn boundaries respected.
- Retrieval: Chunks of 5 turns.

**Config (ama_agent.yaml):**
- `retrieval_mode: "bm25"` — not used in current code; `retrieve_with_qwen` is used.
- `chunk_size: 8192`, `top_k: 5`, `causal: false` (typo `casual` in some configs).

**Caveats:**
- `retrieve_with_llm` and `retrieve_with_embed` exist but are not wired into `memory_retrieve`; only `retrieve_with_qwen` is used.
- `traj_find` and `traj_get` (tool.py) support keyword/action/entity search; used in `retrieve_with_llm`.

---

## 8. Transferability notes

**Reusable:**
- Two-stage retrieval (state first, raw chunks on demand).
- LLM sufficiency judgment.
- Hierarchical chunk compression with state accumulation.
- Structured output markers (`**STATE_MEMORY**`, `**CAUSAL_GRAPH**`).
- LLM relevance scoring for chunks.

**Tightly coupled:**
- Trajectory format (Turn N: Action: / Observation:).
- AMA-Bench interface (`memory_construction`, `memory_retrieve`).
- Synchronous LLM calls.

**Fits best:**
- Long-horizon agent trajectory QA benchmarks.
- Offline construction, per-question retrieval.
- Scenarios where semantic relevance matters more than exact keyword match.

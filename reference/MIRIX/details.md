# Details for MIRIX

Implementation-oriented reference for the MIRIX memory system design.

---

## 1. System architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  TemporaryMessageAccumulator                                     │
│  (screenshots, voice, text) → threshold → absorb_content         │
├─────────────────────────────────────────────────────────────────┤
│  Meta Memory Agent (or direct to 6 agents)                        │
│  Receives message → classifies → trigger_memory_update(types)     │
│  → finish_memory_update                                           │
├─────────────────────────────────────────────────────────────────┤
│  Specialized agents (parallel via message queue):                 │
│  Episodic, Procedural, Resource, Semantic, Core, Knowledge Vault │
│  Each: extract from message → call insert/update/delete tools     │
├─────────────────────────────────────────────────────────────────┤
│  Retrieval: build_system_prompt_with_memories                     │
│  key_words → list/search per memory type (embedding or BM25)      │
│  → format into system prompt → Chat Agent                        │
└─────────────────────────────────────────────────────────────────┘
```

**Components:**
- **Meta Memory Agent**: Routes via `trigger_memory_update`; uses decision framework (1–6 evaluation order).
- **Memory agents**: Core (core_memory_append/replace/rewrite), Episodic (insert/merge/replace), Procedural, Resource, Semantic, Knowledge Vault (each with insert/update/delete tools).
- **TemporaryMessageAccumulator**: `add_message`, `should_absorb_content`, `absorb_content_into_memory`.
- **Memory managers**: EpisodicMemoryManager, ProceduralMemoryManager, etc. — CRUD + list/search.

---

## 2. Data model and schema

**Core Memory:**
- Blocks: persona, human; character limits; append/replace/rewrite; stored in Block ORM.

**Episodic Memory:**
- EpisodicEvent: id, occurred_at, last_modify, actor, event_type, summary, details, tree_path, metadata_; details_embedding, summary_embedding.

**Semantic Memory:**
- SemanticMemoryItem: id, name, summary, details, source, metadata_; embedding.

**Procedural Memory:**
- ProceduralMemoryItem: id, name, summary, steps (JSON), metadata_; embedding.

**Resource Memory:**
- ResourceMemoryItem: id, title, summary, resource_type, content, metadata_; embedding.

**Knowledge Vault:**
- KnowledgeVaultItem: id, entry_type, caption, secret_value, sensitivity (low/medium/high); metadata_ for filtering.

**Update semantics:** Append (core), insert/merge/replace (episodic), insert/update/delete (others). Resource: update existing when same document; merge episodic when continuing same activity.

---

## 3. Storage and indexing

- **SQLite or PostgreSQL**: Configurable per storage type (archival, recall, metadata).
- **Vector embeddings**: Optional (BUILD_EMBEDDING_FOR_MEMORY); stored in details_embedding, summary_embedding, etc.
- **BM25**: PostgreSQL-native or in-memory (rank_bm25) for full-text search.
- **Organization-scoped**: organization_id on all memory tables.
- **Retrieval unit**: Single memory item (event, procedure, resource, etc.).

---

## 4. Retrieval logic

1. **Candidate generation**: 
   - With query: embedding search (cosine similarity) or BM25 (tokenized, cleaned).
   - Without query: list by time (episodic) or list all (others).
2. **Filtering**: organization_id; sensitivity for Knowledge Vault (low/medium for Chat Agent).
3. **Ranking**: BM25 score or vector distance; limit (e.g., MAX_RETRIEVAL_LIMIT_IN_SYSTEM).
4. **Formatting**: Format each memory type into string for system prompt; episodic: recent + relevant (two lists).

---

## 5. Store and update logic

**Store triggers:** `absorb_content_into_memory` when `temporary_message_limit` reached; Chat Agent `trigger_memory_update` for explicit user requests.

**Extraction:** Each memory agent receives message (screenshots, voice transcription, text, user–assistant conversation); uses mode-specific prompts to extract structured data; calls tools (episodic_memory_insert, resource_memory_insert, etc.).

**Deduplication:** Resource: update existing when same document; Episodic: merge when continuing same activity (episodic_memory_merge).

**Validation:** Schema validation via Pydantic; required fields enforced.

---

## 6. Consolidation and lifecycle

- **No automatic consolidation**: Each memory type stores independently.
- **Message summarization**: `summarize_messages` for long sequences (GPT summarizer); used in context window management.
- **Episodic merge**: episodic_memory_merge combines continuing events.
- **Resource update**: When document already exists, update with new content.

---

## 7. Important implementation details

**Key classes:**
- `TemporaryMessageAccumulator.absorb_content_into_memory()`: Builds message, sends to Meta or all agents.
- `trigger_memory_update()`: Routes to specialized agents via message queue; parallel execution.
- `build_system_prompt_with_memories()`: Fetches core, episodic (recent + relevant), resource, procedural, semantic, knowledge_vault; formats into prompt.

**Search methods:** `search_method='embedding'` or `'bm25'`; EpisodicMemoryManager supports both; BM25 uses `_preprocess_text_for_bm25` (tokenize, clean, lowercase).

**Chaining:** `CHAINING_FOR_MEMORY_UPDATE` allows agent to continue reasoning between `trigger_memory_update` calls until `finish_memory_update`.

---

## 8. Transferability notes

**Reusable:**
- Six-memory taxonomy and classification rules.
- Meta Memory router pattern.
- Message accumulation before processing.
- Hybrid search (BM25 + vector).

**Tightly coupled:**
- Letta/MemGPT agent framework.
- Message queue and agent orchestration.
- Client/server architecture.

**Best fit:** Personal assistants with multimodal input; desktop agents with screen observation.

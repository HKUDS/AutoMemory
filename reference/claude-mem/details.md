# Details for Claude-Mem

Implementation-oriented reference for the claude-mem memory system design.

---

## 1. System architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Hook (stdin) → Database → Worker → SDK Processor → Database   │
│  SessionStart ← ContextBuilder ← SessionStore/SessionSearch     │
├─────────────────────────────────────────────────────────────────┤
│  add() flow:                                                     │
│    PostToolUse → PendingMessageStore (queue) → Worker claims     │
│    → SDK Agent (Claude) extracts <observation> XML               │
│    → ResponseProcessor parses → storeObservations (atomic)       │
│    → ChromaSync.syncObservation (fire-and-forget)                │
├─────────────────────────────────────────────────────────────────┤
│  search() flow:                                                  │
│    MCP search → HTTP /api/search → SearchOrchestrator            │
│    → ChromaSearchStrategy (semantic) OR SQLiteSearchStrategy      │
│       OR HybridSearchStrategy (metadata + semantic ranking)       │
│    → ResultFormatter → compact index / full details              │
└─────────────────────────────────────────────────────────────────┘
```

**Components:**
- **Hooks**: context-hook (SessionStart), new-hook (UserPromptSubmit), save-hook (PostToolUse), summary-hook (Stop), cleanup-hook (SessionEnd).
- **Worker**: Express on 37777; SessionQueueProcessor; SDKAgent/GeminiAgent/OpenRouterAgent.
- **SessionStore**: CRUD for sdk_sessions, observations, session_summaries, user_prompts.
- **SessionSearch**: FTS5 queries; findByType, findByConcept, findByFile.
- **ChromaSync**: Embeds observations; queryChroma for semantic search.
- **ContextBuilder**: ObservationCompiler → TimelineRenderer → Header/Footer.

---

## 2. Data model and schema

**sdk_sessions:**
- sdk_session_id, claude_session_id, project, status, prompt_counter, created_at, completed_at.

**observations:**
- id, memory_session_id, project, type, title, subtitle, narrative, facts (JSON), concepts (JSON), files_read (JSON), files_modified (JSON), content_hash, discovery_tokens, created_at.

**Observation types:** decision, bugfix, feature, refactor, discovery, change (configurable via modes).

**session_summaries:**
- request, investigated, learned, completed, next_steps, notes.

**Update semantics:** Append-only for observations; content-hash deduplication (skip if identical within 30s). Summaries overwrite per prompt_number.

---

## 3. Storage and indexing

- **SQLite**: Primary store; WAL mode; bun:sqlite driver.
- **FTS5**: Virtual tables observations_fts, session_summaries_fts, user_prompts_fts; triggers for sync.
- **Chroma**: Optional; embeddings for observations and prompts; hybrid with metadata filters.
- **Retrieval unit**: Single observation or summary; index format is compact (ID, title, type, tokens).

---

## 4. Retrieval logic

1. **Candidate generation**: 
   - With query: Chroma (semantic) or FTS5 (keyword) or Hybrid (metadata filter + Chroma ranking).
   - Without query: Direct SQLite filter (date, type, project).
2. **Filtering**: type, concepts, files, project, dateRange.
3. **Formatting**: ResultFormatter produces table (index) or full observation text.
4. **3-layer workflow**: search → timeline(anchor, depth_before, depth_after) → get_observations(ids).

---

## 5. Store and update logic

**Store triggers:** PostToolUse (each tool execution); Summary hook (session stop).

**Extraction:** Claude Agent SDK receives tool I/O; mode prompts define observation schema (type, title, facts, narrative, concepts, files). Output: XML `<observation>` and `<summary>`.

**Deduplication:** content_hash = hash(memory_session_id, title, narrative); findDuplicateObservation within 30s window.

**Validation:** Parser validates type against mode.observation_types; invalid types rejected.

---

## 6. Consolidation and lifecycle

- **No automatic consolidation**: Each observation stored independently.
- **Session completion**: SessionEnd marks status=completed; no deletion.
- **Summary**: One summary per prompt_number; Stop hook triggers summary generation.
- **Content-hash dedup**: Prevents duplicate observations from rapid re-processing.

---

## 7. Important implementation details

**Key classes:**
- `ResponseProcessor.processAgentResponse()`: Parse XML, storeObservations (atomic), sync Chroma, broadcast SSE.
- `ContextBuilder.buildContextOutput()`: Query observations/summaries, build timeline, render index.
- `SearchOrchestrator`: Selects ChromaSearchStrategy, SQLiteSearchStrategy, or HybridSearchStrategy based on query + filters.

**Observation XML schema:** type, title, subtitle, facts[], narrative, concepts[], files_read[], files_modified[].

**Progressive disclosure:** Index shows ID, time, type emoji, title, ~token_count; footer instructs "Use MCP search to fetch full details on-demand."

**Privacy:** `<private>content</private>` stripped at hook layer before storage.

---

## 8. Transferability notes

**Reusable:**
- Progressive disclosure (index-first, cost-visible) for context injection.
- Observation-based memory (structured extraction from tool usage).
- 3-layer retrieval workflow (search → timeline → get_observations).
- Hybrid search (FTS5 + vector).

**Tightly coupled:**
- Claude Code hook lifecycle and transcript format.
- Claude Agent SDK XML parsing.
- Mode config (observation_types, prompts) in plugin/modes/.

**Best fit:** Coding agents with tool-heavy workflows; projects needing cross-session continuity.

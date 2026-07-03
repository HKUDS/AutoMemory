# Details for LightGrep

Implementation-oriented reference for the LightGrep two-file memory system.

---

## 1. System architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Every LLM call:                                              │
│    ContextBuilder injects MEMORY.md into system prompt         │
│    SessionManager returns messages[last_consolidated:]         │
├──────────────────────────────────────────────────────────────┤
│  When prompt tokens > context_window_tokens:                  │
│    MemoryConsolidator.maybe_consolidate_by_tokens()           │
│    → pick boundary (user-turn edge)                           │
│    → MemoryStore.consolidate(chunk) → LLM save_memory tool    │
│    → append HISTORY.md + overwrite MEMORY.md                  │
│    → advance last_consolidated; repeat if still over target   │
└──────────────────────────────────────────────────────────────┘
```

**Components:** MemoryStore (read/write markdown files, run LLM consolidation), MemoryConsolidator (token estimation, boundary selection, multi-round loop, per-session async locking), ContextBuilder (system prompt injection), SessionManager (JSONL persistence, `last_consolidated` tracking).

---

## 2. Data model and schema

**Session:**
- `key` (string): session identifier
- `messages` (list[dict]): append-only; each has `role`, `content`, `timestamp`, optional `tool_calls`/`tool_call_id`/`name`
- `last_consolidated` (int): pointer to first unconsolidated message index
- Persisted as JSONL (first line = metadata record, then one line per message)

**MEMORY.md:** Full markdown file (~few thousand tokens max). Sections: User Information, Preferences, Project Context, Important Notes. Complete replacement on each consolidation.

**HISTORY.md:** Append-only markdown. Each entry: `[YYYY-MM-DD HH:MM] paragraph...`. Designed for grep searchability with timestamps and keywords.

**save_memory tool output (from LLM):**
- `history_entry` (string): timestamped paragraph for HISTORY.md
- `memory_update` (string): complete new MEMORY.md content

---

## 3. Storage and indexing

- **Backend**: Plain filesystem — two `.md` files per workspace, JSONL for session messages.
- **Indexing**: None. MEMORY.md is loaded fully into system prompt. HISTORY.md is searched by grep.
- **No vector store, no embeddings, no database.**

---

## 4. Retrieval logic

1. **MEMORY.md** (automatic): ContextBuilder reads file, injects as `# Memory\n## Long-term Memory\n{content}` in system prompt. Every LLM call gets full facts.
2. **HISTORY.md** (on-demand): Agent uses shell grep via exec tool. Always-on memory skill teaches the agent grep patterns (`grep -i "keyword" HISTORY.md`).
3. **No ranking, no filtering** — facts are always present; history search is keyword-based.

---

## 5. Store and update logic

**Trigger:** `estimated_prompt_tokens > context_window_tokens` (checked pre- and post- LLM call).

**Consolidation loop** (up to 5 rounds):
1. Estimate tokens. If under full window, skip. Target: half the window.
2. `pick_consolidation_boundary()`: find a user-turn edge that would shed enough tokens. Never splits assistant/tool-call groups.
3. Extract chunk: `messages[last_consolidated:boundary]`.
4. LLM consolidation call: system="You are a memory consolidation agent", user=current MEMORY.md + formatted chunk. Forced `tool_choice` for `save_memory` (fallback to `auto` if provider rejects forced).
5. Parse `save_memory` args → append `history_entry` to HISTORY.md → write `memory_update` to MEMORY.md if changed.
6. Advance `last_consolidated`. Re-estimate. Loop.

**Direct agent writes:** The agent can also edit MEMORY.md directly using file tools for immediate fact capture (e.g., "I prefer dark mode" → agent writes it immediately without waiting for consolidation).

**Failure handling:** After 3 consecutive LLM failures, `_raw_archive()` dumps formatted messages to HISTORY.md with `[RAW]` prefix. Counter resets. Data never lost.

---

## 6. Consolidation and lifecycle

- **No TTL / compaction / archival**: MEMORY.md is bounded by the LLM's judgment (it decides what to keep). HISTORY.md grows without bound (append-only).
- **Implicit dedup**: The LLM sees existing MEMORY.md + new conversation and outputs the merged result. Redundant facts are naturally deduplicated.
- **Implicit conflict resolution**: When new facts contradict old ones, the LLM updates the relevant section. No explicit contradiction detection.
- **Session-level locking**: `WeakValueDictionary` of `asyncio.Lock` prevents concurrent consolidation of the same session.

---

## 7. Important implementation details

- **Boundary selection**: Always cuts at user-turn boundaries to avoid orphaned tool results or split assistant responses.
- **Token estimation chain**: Try provider's native counter first → fallback to tiktoken `cl100k_base`. Counts content, tool call JSON, tool_call_id, and name fields.
- **Prompt cache friendliness**: Messages are append-only; `last_consolidated` advances but never mutates the list. Historical messages retain their positions for prefix caching.
- **Message formatting for consolidation**: `[{timestamp[:16]}] {ROLE} [tools: {tool1, tool2}]: {content}` — compact but preserves tool-call context.
- **Provider-agnostic**: Works with any LLM provider supporting tool calling. Forced `tool_choice` with graceful fallback.

---

## 8. Transferability notes

**Reusable:**
- Two-file fact/event separation pattern (any agent needing always-available facts + searchable history).
- Token-pressure-driven consolidation with multi-round loop (any system managing context window).
- Full-replacement memory via LLM (when memory size is manageable and explicit dedup is overkill).
- Raw archive fallback (resilience pattern for any LLM pipeline).
- Append-only session with cursor (prompt cache optimization).

**Tightly coupled:**
- Requires the agent to have file-system access and exec tools for grep-based history search.
- MEMORY.md must fit in system prompt; doesn't scale to large fact corpora.

**Best fit:** Lightweight single-user agents needing persistent memory with minimal infrastructure. Systems where simplicity and debuggability outweigh retrieval sophistication.

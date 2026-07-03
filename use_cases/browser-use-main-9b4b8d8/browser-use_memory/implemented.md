# Memory Implementation: browser-use

## Memory Architecture

The implementation adds a decoupled `browser_use.memory` package and a thin integration layer in `Agent`.

The memory package does not import Browser-Use agent internals. It accepts serialized history dictionaries from the agent, stores typed Pydantic records in SQLite, retrieves relevant records by task/domain, and formats a compact `<memory_context>` block for prompt injection.

Runtime workflow:

1. `Agent(..., memory_path=...)` or `Agent(..., memory=True)` creates a `BrowserUseMemory` instance.
2. Before each LLM decision, `Agent._render_memory_context()` calls `memory.recall(task, current_url)` and injects formatted recall text into `AgentMessagePrompt`.
3. After each finalized step, `Agent._record_memory_step()` passes the latest serialized `AgentHistory` item to memory for site-friction and task-snapshot updates.
4. In `Agent.run()` cleanup, `Agent._commit_memory_history()` passes the full serialized `AgentHistoryList` to memory exactly once.
5. `BrowserUseMemory.commit_history()` archives the raw trace, performs rigorous extraction, optionally performs real-LLM extraction, deduplicates/merges records, and persists all memory types.

Default behavior is opt-in. No memory is used unless `memory`, `memory_path`, or `BROWSER_USE_MEMORY_DIR` is provided. `Agent(memory=True)` stores data under Browser-Use config directory `memory/`.

## Memory Implemented

### Source Files

- `browser_use/memory/views.py`: Pydantic models for all memory records and result containers.
- `browser_use/memory/store.py`: SQLite persistence, deduplication, merge/update semantics, and lightweight retrieval.
- `browser_use/memory/service.py`: high-level write/read/recall/format API, rigorous extraction, optional LLM extraction, trace compaction for memory writes.
- `browser_use/memory/__init__.py`: public import surface.
- `browser_use/agent/service.py`: optional memory configuration, per-step write, session-end flush, recall formatting.
- `browser_use/agent/prompts.py`: `<memory_context>` injection into the state message.
- `browser_use/agent/message_manager/service.py`: passes memory context through to `AgentMessagePrompt`.
- `tests/ci/test_memory_system.py`: functional tests for round-trip, dedup/update, re-init lifecycle, prompt injection, and real OpenRouter LLM extraction.

### Key Decisions

- SQLite is the durable backend. It persists every record as validated JSON and keeps a unique `dedupe_key` per memory type.
- Retrieval is lightweight keyword/domain scoring. No embedding dependency was added, so there are no precomputed embedding artifacts.
- LLM extraction is implemented but not required for core persistence. It asks the model for a compact `LLMMemoryDraft`, then converts that draft into full memory records locally. If a provider does not support structured JSON schema, memory retries with a plain-JSON prompt against the same real provider.
- Agent integration uses existing lifecycle points rather than model-visible memory tools. The browsing model is not asked to decide when to save memory.
- Prompt injection is advisory. The injected context explicitly tells the agent to verify source/page state before final reporting.

## Memory Type Coverage

### Navigation Shortcut Memory

Implemented as `NavigationShortcut` with `RouteStep` records. Written on successful session flush from serialized action history. The extractor stores semantic summaries and anchor text from `interacted_element`, not raw indexes alone. Dedup key is task intent + domain scope + success condition. Recall returns top matching shortcuts by task/domain.

### Obtained Information Cache

Implemented as `ObtainedInformationCache`. Written on successful session flush from final `done`/extracted content and source URLs. Each cache entry includes answer, source URL, snippet, freshness policy, confidence, verification recipe, and trace id. Dedup key is query signature + source URL + answer.

### Site Friction and Recovery Memory

Implemented as `SiteFrictionMemory`. Written from per-step action errors and full-session extraction. The classifier recognizes readiness timeouts, captcha/cloudflare, blocked/access denied, failed clicks, empty DOM, PDF viewer, and login redirects. Dedup key is site pattern + problem type + symptom.

### Task Progress Snapshot

Implemented as `TaskProgressSnapshot`. Written after each finalized step and session flush. It records task id, original task, current goal, completed/pending items, current URL/tabs, last successful action, and blocking issue. Dedup key is task id, so newer snapshots overwrite older ones.

### Session Trace Archive

Implemented as `SessionTraceArchive`. Every session flush writes the raw serialized history to `<memory_dir>/traces/<trace_id>.json` and stores an indexed trace summary in SQLite. This record is not injected into normal prompts; it is available for refine/debug drill-down.

## Known Limitations

- Retrieval uses lexical/domain scoring, not embeddings. The design allowed optional embeddings; this implementation keeps the backend dependency-free.
- LLM extraction depends on the configured provider's ability to produce a small JSON draft when structured output is unavailable. The test uses the real OpenRouter provider and passed, but exact generated record wording may vary.
- Cache freshness is heuristic. The implementation marks current/latest/price/weather/score-style tasks as volatile and repository/license/documentation-style tasks as slow-changing.
- Site friction is currently extracted from action errors. Non-error warning logs, such as page-readiness warnings that still produce successful action results, are not captured unless they appear in serialized action results.
- `memory_profile_scope` is stored on `Agent` for API shape but not yet used to partition records. Current scoping is represented inside records as `sensitivity_scope='profile'`.
- The prompt injection path is opt-in through memory configuration. Existing `run.py` remains memory-disabled unless changed by the caller.

## Memory Components

### Component: BrowserUseMemory

- `purpose`: High-level memory facade for recall, prompt formatting, per-step writes, and session/window flushes.
- `write_method`: `from browser_use.memory import BrowserUseMemory; await BrowserUseMemory(data_dir).commit_history(task: str, history_payload: dict, llm: BaseChatModel | None = None, task_id: str | None = None, session_id: str | None = None)`
- `write_trigger`: system_trigger
- `read_method`: `BrowserUseMemory.recall(task: str, current_url: str | None = None, include_snapshots: bool = False)`
- `read_returns`: `MemoryRecall` with lists of shortcuts, caches, site notes, and task snapshots.
- `eval_mode`: qa
- `persistence`: `<data_dir>/browser_use_memory.sqlite3` plus `<data_dir>/traces/*.json`
- `requires_flush_window`: true
- `flush_window_method`: `await BrowserUseMemory.flush_window(task: str, window_payload: dict, llm: BaseChatModel | None = None, task_id: str | None = None, session_id: str | None = None)`
- `dependencies`: MemoryStore, MemoryExtraction schemas, optional real LLM provider
- `notes`: `commit_history` uses rigorous extraction always and LLM extraction when `llm` is supplied and the trace succeeded.

### Component: MemoryStore

- `purpose`: Durable SQLite persistence and dedup/update semantics for all memory records.
- `write_method`: `from browser_use.memory import MemoryStore; MemoryStore(data_dir).upsert_shortcut(record)` and analogous `upsert_cache`, `upsert_site_note`, `upsert_task_snapshot`, `archive_trace`
- `write_trigger`: system_trigger
- `read_method`: `MemoryStore(data_dir).list_shortcuts()` and analogous list/search methods
- `read_returns`: Lists of typed Pydantic records; search methods return ranked top-k lists.
- `eval_mode`: hit_rate
- `persistence`: `<data_dir>/browser_use_memory.sqlite3`
- `requires_flush_window`: false
- `dependencies`: Pydantic record models
- `notes`: Dedup is deterministic and lexical. Shortcut success counts accumulate across duplicate writes.

### Component: Navigation Shortcut Memory

- `purpose`: Stores reusable successful browser routes as semantic action recipes.
- `write_method`: `BrowserUseMemory.commit_history(...);` internally writes via `MemoryStore.upsert_shortcut(record: NavigationShortcut)`
- `write_trigger`: system_trigger
- `read_method`: `BrowserUseMemory.recall(task, current_url).shortcuts` or `BrowserUseMemory.read_shortcuts()`
- `read_returns`: `list[NavigationShortcut]` with route steps, anchors, domains, success condition, source trace id, and success/failure counts.
- `eval_mode`: hit_rate
- `persistence`: SQLite `memories` table rows with `kind='shortcut'`
- `requires_flush_window`: true
- `flush_window_method`: `await BrowserUseMemory.flush_window(task, window_payload, llm=None, task_id=None, session_id=None)`
- `dependencies`: Session Trace Archive for source evidence
- `notes`: The validator rejects shortcuts with no route, no domain, or click-like steps lacking semantic anchors/summaries.

### Component: Obtained Information Cache

- `purpose`: Stores source-grounded answers and extracted facts for future tasks.
- `write_method`: `BrowserUseMemory.commit_history(...);` internally writes via `MemoryStore.upsert_cache(record: ObtainedInformationCache)`
- `write_trigger`: system_trigger
- `read_method`: `BrowserUseMemory.recall(task, current_url).caches` or `BrowserUseMemory.read_caches()`
- `read_returns`: `list[ObtainedInformationCache]` with query signature, answer, source URL, snippet, observed time, freshness policy, confidence, and verification recipe.
- `eval_mode`: qa
- `persistence`: SQLite `memories` table rows with `kind='cache'`
- `requires_flush_window`: true
- `flush_window_method`: `await BrowserUseMemory.flush_window(task, window_payload, llm=None, task_id=None, session_id=None)`
- `dependencies`: Session Trace Archive for raw source trace
- `notes`: The agent prompt labels cached facts as advisory and requires verification before final reporting.

### Component: Site Friction and Recovery Memory

- `purpose`: Stores durable site-specific problems and recovery hints.
- `write_method`: `await BrowserUseMemory.record_step(task: str, step_payload: dict, task_id: str | None = None)` and `BrowserUseMemory.commit_history(...)`
- `write_trigger`: system_trigger
- `read_method`: `BrowserUseMemory.recall(task, current_url).site_notes` or `BrowserUseMemory.read_site_notes()`
- `read_returns`: `list[SiteFrictionMemory]` with site pattern, problem type, symptom, failed actions, recovery text, and success/failure counts.
- `eval_mode`: hit_rate
- `persistence`: SQLite `memories` table rows with `kind='site_note'`
- `requires_flush_window`: false
- `dependencies`: serialized per-step action results
- `notes`: Best tested with traces containing explicit `ActionResult.error` values.

### Component: Task Progress Snapshot

- `purpose`: Stores resumable partial progress without treating it as permanent factual memory.
- `write_method`: `await BrowserUseMemory.record_step(task, step_payload, task_id=None)` and `BrowserUseMemory.commit_history(...)`
- `write_trigger`: system_trigger
- `read_method`: `BrowserUseMemory.recall(task, include_snapshots=True).task_snapshots` or `BrowserUseMemory.read_task_snapshots()`
- `read_returns`: `list[TaskProgressSnapshot]` with current goal, completed/pending items, URL/tabs, available files, last action, and blocking issue.
- `eval_mode`: qa
- `persistence`: SQLite `memories` table rows with `kind='task_snapshot'`
- `requires_flush_window`: false
- `dependencies`: serialized per-step or full-session history
- `notes`: Normal fresh-task recall does not include snapshots unless `include_snapshots=True`.

### Component: Session Trace Archive

- `purpose`: Preserves raw serialized trace evidence and compact summaries for refine/debug.
- `write_method`: `BrowserUseMemory.commit_history(...);` internally writes via `MemoryStore.archive_trace(record: SessionTraceArchive, raw_trace: dict)`
- `write_trigger`: system_trigger
- `read_method`: `BrowserUseMemory.read_traces()`
- `read_returns`: `list[SessionTraceArchive]` with raw trace path, summary, task, URLs, action names, and final result.
- `eval_mode`: qa
- `persistence`: SQLite `memories` table rows with `kind='trace'` and raw JSON files under `<data_dir>/traces/`
- `requires_flush_window`: true
- `flush_window_method`: `await BrowserUseMemory.flush_window(task, window_payload, llm=None, task_id=None, session_id=None)`
- `dependencies`: none
- `notes`: Raw traces are not injected into the browsing prompt by default.

### Component: Agent Memory Integration

- `purpose`: Connects Browser-Use lifecycle to the decoupled memory module.
- `write_method`: `Agent._record_memory_step()` and `Agent._commit_memory_history()`
- `write_trigger`: system_trigger
- `read_method`: `Agent._render_memory_context(current_url)`
- `read_returns`: A formatted `<memory_context>` string or `None`.
- `eval_mode`: qa
- `persistence`: Delegates to configured `BrowserUseMemory` data directory.
- `requires_flush_window`: true
- `flush_window_method`: `Agent._commit_memory_history()`
- `dependencies`: BrowserUseMemory
- `notes`: Enabled by `Agent(memory=True)`, `Agent(memory_path=...)`, `Agent(memory=<BrowserUseMemory instance>)`, or `BROWSER_USE_MEMORY_DIR`.

### Component: Memory Prompt Injection

- `purpose`: Makes recalled memory visible to the model at inference time.
- `write_method`: none
- `write_trigger`: none
- `read_method`: `AgentMessagePrompt(..., memory_context='<memory_context>...</memory_context>').get_user_message(use_vision=False)`
- `read_returns`: `UserMessage` whose text contains `<memory_context>` before `<browser_state>`.
- `eval_mode`: qa
- `persistence`: none
- `requires_flush_window`: false
- `dependencies`: MessageManager and AgentMessagePrompt
- `notes`: Fixture-only component for refine tests that inspect prompt visibility.

# Memory Design: browser-use

## Agent Summary

Browser-Use is a browser automation agent that starts a `BrowserSession`, builds a current browser state from CDP/DOM/screenshot data, asks an LLM for structured `AgentOutput`, executes one or more typed `Tools` actions, records an `AgentHistory` item, and repeats until `done`.

The local open-source agent already has strong in-run memory:

- `AgentOutput.memory` is generated every step and included in later in-run prompt history.
- `ActionResult.long_term_memory` and non-one-time `extracted_content` are carried into later in-run history.
- `ActionResult.include_extracted_content_only_once=True` puts large/read-only outputs into `<read_state>` for the next step only.
- `AgentHistoryList` stores model outputs, actions, action results, URLs, interacted elements, screenshots, and state messages after a run.
- `MessageCompactionSettings` can summarize older in-run steps once the history text becomes large.

The missing system is cross-session memory. A new `Agent(...)` starts from empty task/history state unless the caller explicitly injects an `AgentState`. The returned history can be saved, but Browser-Use does not automatically turn past trajectories into navigation shortcuts or obtained-information cache entries.

Two live runs through `uv run python run.py` used `ChatOpenRouter` from `.env` (`qwen/qwen3.5-flash-02-23`) for the task "Find license of the browser-use repo". Both runs navigated to `https://github.com/browser-use/browser-use`, clicked the `LICENSE` file, and reported MIT License. The second run repeated the same trajectory instead of using the first run as a shortcut or cache.

## Retention Window

The inference-time window is rebuilt each step by `MessageManager.create_state_messages()` as one system message plus one state message containing `<agent_history>`, `<agent_state>`, `<browser_state>`, optional `<read_state>`, and page-specific actions.

Default behavior:

- `max_history_items=None`: all in-run `agent_history_items` remain in prompt history until compaction or session end.
- If `max_history_items` is set: the prompt keeps the first history item, an omitted-count marker, and the most recent `max_history_items - 1` items.
- Message compaction defaults to enabled through `Agent(message_compaction=True)`: every 25 steps, if history text is at least 40000 characters, summarize older history into `compacted_memory`, keep first item plus last 6 items, and cap the summary at 6000 characters.
- `<read_state>` is one-step context for extraction/read outputs and is capped at 60000 characters before truncation.
- At session boundary, all prompt-visible history is lost unless the caller manually saves or reinjects state.

## Gap Analysis

### Gap 1: Repeated Navigation Work

Information lost: successful routes from task intent to target page, including start URL, search/navigate path, clicks, scrolls, selectors, page fingerprints, and confirmation conditions.

When lost: session boundary. It can also become less useful inside a long run when middle history is omitted or compacted.

Impact: repeated tasks spend steps rediscovering the same site path. The observed `run.py` task repeated GitHub navigation and `LICENSE` click on the second run.

Root cause: missing cross-session trajectory representation and recall point. Existing history is a returned artifact, not a searchable memory.

### Gap 2: Obtained Facts Are Not Cached

Information lost: source-grounded facts found during browsing, such as "browser-use repo license is MIT", source URL, observed text, and confidence.

When lost: session boundary; also when one-time read/extract output leaves `<read_state>`.

Impact: the agent reopens pages and reruns extraction for stable facts. Users pay latency and model/tool cost repeatedly.

Root cause: extracted content is stored as in-run prompt text/history only; there is no durable fact cache with freshness and source metadata.

### Gap 3: Tool Results Are Too Noisy to Reuse Directly

Information lost or mishandled: raw action history contains local element indexes, transient screenshots, long DOM snippets, and page-load artifacts. These are not stable enough to replay directly.

When lost: either not persisted, or persisted in `AgentHistoryList` without normalization into reusable route/fact records.

Impact: even if history files are saved, an implementer cannot safely inject them as-is. Element indexes like GitHub `LICENSE` index 300 can change.

Root cause: representation issue. Browser-Use records detailed trace data but does not derive semantic anchors or reusable action recipes.

### Gap 4: Site-Specific Friction Is Not Learned Across Runs

Information lost: page readiness timeouts, captcha/blocking patterns, failed clicks, broken selectors, and successful recovery actions.

When lost: end of run. The in-run loop detector and error memories are not durable.

Impact: the agent can repeat known-bad approaches on a site instead of trying the known workaround earlier.

Root cause: missing durable agent-experience memory.

### Gap 5: Interrupted Long Tasks Cannot Resume Intelligently

Information lost: partial progress, completed subtasks, unresolved items, current URLs/tabs/files, and next action.

When lost: process exit or new session without `injected_agent_state`.

Impact: users must restate context or the agent restarts from scratch.

Root cause: task state exists inside `AgentState`, `FileSystem`, and prompt history but is not committed as a resumable memory snapshot.

## Design Decisions

### Weight Class

Use a medium-weight supplementary memory system.

Rationale: Browser-Use is latency-sensitive and should not add multiple LLM calls to every browser step. But the target behavior, trajectory shortcuts and information cache, needs structured durable storage, relevance retrieval, deduplication, and freshness handling. The default path should add zero LLM calls per ordinary step, one lightweight retrieval at task start, and one extraction/compression pass at session end. Optional embeddings or LLM reranking can improve quality without being required for the minimal implementation.

### Memory Type 1: Navigation Shortcut Memory

Purpose: reuse successful prior trajectories as web navigation shortcuts.

Stored representation:

- `shortcut_id`
- `task_intent`: normalized natural-language intent
- `domain_scope`: canonical domains involved
- `start_conditions`: start URL/domain, auth/profile hints, required files or user inputs
- `route_steps`: ordered semantic actions, not raw-only indexes
- `anchors`: visible text, aria label, role, href/path, CSS/XPath fallback, interacted element summary
- `page_fingerprints`: URL pattern, title pattern, stable DOM text hashes or key visible strings
- `success_condition`: what must be observed before considering the shortcut complete
- `source_trace_id`, `created_at`, `last_verified_at`, `success_count`, `failure_count`
- `sensitivity_scope`: user/profile/global and whether auth was involved

Write actor: system-triggered at `register_done_callback` after successful runs. The extractor reads `AgentHistoryList.action_history()`, URLs, state messages, interacted elements, and successful final result.

Write timing: session end for successful or partially successful runs. For very long tasks, additionally after compaction boundaries or every N successful route segments.

Write method: intelligent extraction for semantic route recipe, backed by rigorous validators. The validator rejects shortcuts without success condition, source domain, or at least one stable anchor per click/form action.

Update semantics: merge by normalized intent + domain + success condition. Increment success/failure stats. Replace unstable anchors if a later successful trace confirms better anchors. Keep a short version history so bad merges can be diagnosed.

Recall timing: before the first LLM decision, after task normalization and direct URL extraction but before `MessageManager.create_state_messages()` builds the first state prompt. Also recall after navigation to a matching domain if no startup shortcut matched.

Recall method: candidate generation by domain/task keyword/BM25; optional embedding similarity over `task_intent` and route descriptions; rank by intent similarity, domain match, freshness, success rate, and profile compatibility.

Injection point: a new `<memory_context>` block in the state message, before `<browser_state>` or inside `<agent_state>`, formatted as candidate shortcuts with explicit verification instructions:

```text
<memory_context>
Relevant prior navigation shortcuts:
1. Intent: find browser-use repository license
   Use only as a shortcut candidate. Verify current page before final answer.
   Route: navigate https://github.com/browser-use/browser-use -> open LICENSE by link text/aria label "LICENSE"
   Success condition: page shows MIT License text and repository path browser-use/browser-use.
</memory_context>
```

Budget fit: inject at most 3 shortcuts, each under about 1000 characters. Do not inject raw screenshots or full DOM.

### Memory Type 2: Obtained Information Cache

Purpose: avoid re-extracting stable, source-grounded facts.

Stored representation:

- `cache_id`
- `query_signature`: normalized user question or extraction query
- `answer`: concise factual answer
- `source_url`
- `source_quote_or_snippet`: short grounding snippet or field path
- `observed_at`
- `freshness_policy`: static, slow-changing, volatile, or task-specified TTL
- `confidence`: derived from tool evidence and final success
- `verification_recipe`: how to quickly revalidate if needed
- `source_trace_id`
- `sensitivity_scope`

Write actor: system-triggered at session end from successful `done` outputs, `extract`, `search_page`, `find_elements`, `read_file`, and non-one-time `extracted_content`. The model should not have to call a memory tool during browsing.

Write timing: after final success. For expensive page extraction outputs, also write when content leaves `<read_state>` or before compaction if the run continues.

Write method: intelligent extraction into a Pydantic schema, with rigorous checks that every cache fact has source URL and observed evidence. If no source is available, archive it as trajectory summary, not fact cache.

Update semantics: deduplicate by source URL + query signature + normalized answer. Static facts can overwrite if source evidence changes; volatile facts create timestamped versions and prefer fresh entries at recall.

Recall timing: startup task analysis. If the cache is sufficient and freshness policy passes, inject it as a candidate answer. For tasks that require live browsing or current data, inject only the verification recipe.

Recall method: query normalization + keyword/BM25 over query/answer/source; optional semantic search. Add a sufficiency gate: if a high-confidence static cache exactly answers the task, the agent may answer after minimal verification or with explicit cached-source wording if the task allows; otherwise it uses the cache to navigate faster.

Injection point:

```text
<memory_context>
Relevant obtained-information cache:
1. Query: license of browser-use repo
   Cached answer: MIT License
   Source: https://github.com/browser-use/browser-use/blob/main/LICENSE
   Observed: 2026-05-11
   Freshness: slow-changing; verify if user asks for latest/current.
</memory_context>
```

Budget fit: inject at most 5 cache entries, each under about 700 characters.

### Memory Type 3: Site Friction and Recovery Memory

Purpose: remember operational experience that helps future navigation.

Stored representation:

- `site_pattern`
- `problem_type`: readiness_timeout, captcha, blocked, failed_click, selector_instability, empty_dom, pdf_viewer, login_redirect
- `symptom`
- `failed_actions`
- `successful_recovery`
- `applicability_conditions`
- `last_seen_at`, `success_count`, `failure_count`

Write actor: system-triggered from action errors, `BrowserError.long_term_memory`, loop detector nudges, timeout handling, and successful recovery traces.

Write timing: post-step for significant failures and session end for confirmed recovery patterns.

Write method: mostly rigorous extraction from structured errors/action results, with optional LLM summarization at session end.

Update semantics: merge by site pattern + problem type; increment stats; decay old entries.

Recall timing: when current URL/domain matches, before state prompt generation for that step.

Recall method: exact domain/path match first, then broader domain pattern. Rank by recency and success rate.

Injection point: `<memory_context>` as "site notes", explicitly separated from facts:

```text
Site note for github.com: page readiness timeout can occur after navigate, but DOM may still be usable. Verify visible file list before deciding navigation failed.
```

Budget fit: at most 3 short site notes per step.

### Memory Type 4: Task Progress Snapshot

Purpose: resume interrupted tasks without treating partial progress as permanent factual memory.

Stored representation:

- `task_id/session_id`
- `original_task`
- `current_goal`
- `completed_items`
- `pending_items`
- `current_url/tabs`
- `available_files`
- `last_successful_action`
- `blocking_issue`
- `created_at/updated_at`

Write actor: system-triggered at session end, stop, exception, and compaction boundaries.

Write timing: after each completed step for long tasks, and always on abnormal termination.

Write method: intelligent compression from recent `agent_history_items`, current plan, file system state, and browser state.

Update semantics: one active snapshot per task/session; overwrite with newer snapshot; archive final snapshot when task succeeds.

Recall timing: only when user starts a follow-up/resume task or supplies a matching task/session id. Do not inject unrelated snapshots into fresh tasks.

Recall method: exact task/session id first; otherwise task similarity and recency.

Injection point: `<memory_context>` under "resumable task state".

Budget fit: one snapshot, under about 2000 characters.

### Memory Type 5: Session Trace Archive

Purpose: preserve compressed and raw evidence for future debugging, shortcut extraction, and cache repair.

Stored representation:

- raw or redacted `AgentHistoryList` path
- compact trajectory summary
- action sequence with results
- URLs and screenshot paths
- extraction/cache records produced from the trace
- sensitive-data redaction metadata

Write actor: system-triggered at session end.

Write timing: every run, successful or failed, subject to retention policy.

Write method: rigorous serialization for raw trace; intelligent compression for summary.

Update semantics: append-only archive with retention limits. Do not inject raw archive by default.

Recall timing: only for drill-down when shortcut/cache/site-note retrieval is insufficient, or when debugging/evaluation asks for trace evidence.

Recall method: progressive disclosure: search summary/index first, then load full observations or raw trace only when needed.

Injection point: normally none; used by memory tooling or implementer diagnostics.

Budget fit: never inject raw trace into ordinary agent prompt.

## System Flow

### Write Flow

1. During a run, collect step data from existing `AgentHistory`, action results, browser state summaries, and callbacks.
2. On each significant failure, record candidate site-friction evidence.
3. On compaction or long-running step intervals, update a task progress snapshot.
4. On `register_done_callback`, process the final `AgentHistoryList`.
5. If success is true, extract navigation shortcuts and obtained-information cache entries.
6. For all outcomes, archive a redacted trace summary.
7. Deduplicate and merge records before commit.

### Recall Flow

1. At Agent initialization, normalize the user task and detect direct URL/domain hints.
2. Retrieve candidate cache entries, shortcuts, and relevant site notes.
3. Apply freshness and sensitivity filters.
4. Keep results small and inject them into `<memory_context>`.
5. Instruct the browsing model that memory is advisory. It may use shortcuts to reduce navigation, but it must verify source/page state before final reporting unless the API explicitly allows cached answers.

## Gap Coverage

### Gap 1: Repeated Navigation Work

Addressed by Navigation Shortcut Memory.

Why sufficient: successful trajectories are captured at session end before the returned history is forgotten, converted into stable semantic route recipes, and recalled before the first LLM decision. For the observed license task, the second run could receive "navigate repo URL, open LICENSE by link text" immediately.

Remaining fall-through: websites can redesign pages, link labels can change, auth/profile scope may differ, and task phrasing may miss retrieval. The route must be verified against current browser state.

### Gap 2: Obtained Facts Are Not Cached

Addressed by Obtained Information Cache.

Why sufficient: successful final answers and extraction outputs are committed with source URL, evidence, and freshness policy. Stable facts like a repository license can be recalled instead of rediscovered from scratch.

Remaining fall-through: volatile data should not be answered from old cache. The design requires freshness policy and verification recipe.

### Gap 3: Tool Results Are Too Noisy to Reuse Directly

Addressed by Navigation Shortcut Memory plus Session Trace Archive.

Why sufficient: raw traces are preserved for evidence, but ordinary recall uses normalized semantic anchors rather than raw element indexes or full DOM. This avoids replaying brittle values like a transient click index.

Remaining fall-through: some sites expose poor semantic anchors; such shortcuts should be lower confidence or require live search.

### Gap 4: Site-Specific Friction Is Not Learned Across Runs

Addressed by Site Friction and Recovery Memory.

Why sufficient: Browser-Use already emits structured errors and long-term action memories; the design persists them by domain and recalls them when the agent returns to the same site.

Remaining fall-through: old friction memories can become stale or over-bias the agent. Entries need recency, decay, and success/failure stats.

### Gap 5: Interrupted Long Tasks Cannot Resume Intelligently

Addressed by Task Progress Snapshot.

Why sufficient: snapshots are written before session state is lost and recalled only for explicit resume/follow-up contexts.

Remaining fall-through: browser tab state may not be restorable if cookies/session/profile are unavailable. Snapshot recall should state what can be resumed and what must be re-navigated.

## Open Questions

- Storage backend belongs to implementation. SQLite plus JSON columns is the conservative default; embeddings can be optional.
- Memory enablement should likely be opt-in at first, for example `Agent(memory=...)`, to avoid surprising persistence.
- Cache answer policy needs a product decision: allow direct cached answers for static facts, or always browse and use cache only as a shortcut. The safer default is shortcut plus verification.
- Privacy boundaries need final API design: user-scoped, browser-profile-scoped, and project-scoped memories should not mix by default.
- Cloud and local memory may need separate adapters. The local design should not assume Browser Use Cloud hosted memory exists.

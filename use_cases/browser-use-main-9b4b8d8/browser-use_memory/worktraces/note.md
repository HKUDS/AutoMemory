# note.md

Your persistent working memory for this pipeline. Write anything here that
you need to remember across steps — there are no formatting rules, required headers, 
and no one grades this file. Optimize it for your own future use. This may include:

---

## User Requirements
- Target repo: /Users/fang/Documents/codespace/browser-use.
- Requested design: "supplement memory system such that previous trajectory can be used as web navigation shortcut and obtained-information cache".
- Output artifacts required by Auto_Memory design workflow:
  - browser-use_memory/worktraces/note.md
  - browser-use_memory/memory_design.md
- Need explore actual Browser-Use agent lifecycle, existing memory/history/session behavior, retention window, then write an actionable design handoff. Avoid implementation details beyond design decisions.

## Checkpoints
- [x] Create note.md from scaffold.
- [x] Inspect README, entry points, dependency/config files.
- [x] Inspect agent loop, history/context assembly, browser/session persistence, tool/action result handling.
- [x] Run or attempt 1-2 simple agent tasks and record practical constraints.
- [x] Consult Auto_Memory reference catalog for relevant trajectory/cache/compaction patterns.
- [x] Write complete memory_design.md and update this note with design summary.
- [x] Implement memory system in `browser_use/memory`.
- [x] Integrate optional memory into Agent lifecycle and prompt context.
- [x] Add functional tests, including real OpenRouter LLM extraction.
- [x] Initialize refine_state.json and write implemented.md.

## Agent Observations
- [observations of the target agent's behavior]
- Browser-Use is a web automation agent: Agent.run starts BrowserSession, executes initial URL action if a single URL is found, repeatedly prepares context, calls LLM for AgentOutput, executes Tools actions, finalizes AgentHistory, emits events, then closes browser unless keep_alive.
- README says Browser Use Cloud hosted agent has persistent filesystem and memory, but the open-source local Agent does not expose a cross-session memory system.
- Agent output schema already has per-step `memory`; this is logged and inserted into in-run agent history.
- `ActionResult` has `long_term_memory` and `extracted_content`; MessageManager turns these into future prompt history/read_state during the same run.
- `AgentHistoryList` is returned from run and can be saved with `save_to_file`, but Agent does not automatically load prior histories for a new task.
- `save_conversation_path` writes per-step conversation text. It is debug/audit persistence, not recall.
- Agent has callbacks: `register_new_step_callback` and `register_done_callback`, good hooks for supplemental memory without changing every tool.
- `run.py` uses local Chromium headless and `ChatOpenRouter(model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"), api_key=os.getenv("OPENROUTER_API_KEY"))`.
- Live `.env` OpenRouter model observed via logs: `qwen/qwen3.5-flash-02-23`.
- `uv run python run.py` task "Find license of the browser-use repo" completed in 3 steps: navigate GitHub repo -> click LICENSE index 300 -> done MIT License.
- Repeating `uv run python run.py` performed the same navigation/click sequence again. No cross-session shortcut or cached obtained-information recall was present.

## Discoveries
- [Key observations and insights on target agent's system]
- Context prompt is one system message plus one current state message. State message contains `<agent_history>`, `<agent_state>`, `<browser_state>`, optional `<read_state>`, page-specific actions, and current screenshot if configured.
- Browser state is current-only: URL, title, tabs, page stats, page info, interactive elements, optional recent events/screenshot.
- History item string contains evaluation, model memory, next goal, and action results, but when `max_history_items` is set it keeps first item + recent items and omits middle steps.
- Existing loop detector tracks recent action hashes and page fingerprints only inside AgentState; it is not durable.
- Existing plan state is in AgentState and not durable across new processes unless injected_agent_state is supplied manually.
- Browser/profile storage can preserve cookies/local auth, but that is not task trajectory memory or answer cache.
- Reference catalog useful patterns:
  - `cm-lifecycle-hooks`: use SessionStart/PostToolUse/Stop hooks for automatic capture.
  - `cm-tool-observation`: tool I/O is better memory source than raw dialogue for tool-heavy agents.
  - `ama-state-compression`: compress trajectory chunks into compact state.
  - `ama-sufficiency-gate`: ask if retrieved/cached context is enough before expensive retrieval.
  - `ov-vector-llm-dedup`: deduplicate/merge similar memories.
  - `lg-token-pressure-consolidation`: token-pressure compaction, but Browser-Use already has step/char compaction.

## Retention Window Mechanics
- [how eviction works, concrete trigger thresholds, key numbers]
- Inference-time retention is not full message transcript. It is rebuilt every step by `MessageManager.create_state_messages`.
- Default `max_history_items=None`: all in-run `agent_history_items` stay visible unless message compaction triggers.
- If `max_history_items` is set: first history item + omitted-count marker + most recent `max_history_items - 1` items are visible; middle steps are dropped from prompt text.
- `MessageCompactionSettings` default enabled when Agent(message_compaction=True): compact every 25 steps if history text >= 40000 chars (~10k tokens); keep first item + last 6; summary max 6000 chars; read_state excluded by default.
- `ActionResult.include_extracted_content_only_once=True` puts content into `<read_state>` for only the next state message; default read_state cap 60000 chars, then truncated.
- `ActionResult.extracted_content` without one-time flag and `long_term_memory` are retained in in-run history until max_history_items/compaction/session boundary.
- Session boundary: new Agent process starts from empty MessageManagerState unless explicit injected_agent_state is supplied. Returned history is not automatically recalled.

## Design Decisions
- [memory type, memorize logic, recall logic, recall strategy, etc]
- Weight class should be medium: Browser-Use is latency-sensitive browser automation, but trajectory reuse needs more than flat logs. Use structured local store + optional embeddings/LLM reranking, with at most one recall LLM gate and one session-end extraction/compression call by default.
- Memory types:
  1. Navigation Shortcut Memory: durable route recipes from successful trajectories, keyed by domain/task intent/start URL/page fingerprints.
  2. Obtained Information Cache: factual answers and extracted page facts from successful runs/tool outputs, keyed by canonical source URL + query/fact signature + freshness policy.
  3. Site Friction/Recovery Memory: captcha, readiness timeout, blocked page, selector/index instability, alternative successful routes.
  4. Task Progress Snapshot: resumable partial state for interrupted long tasks, not a permanent user profile.
  5. Session Trace Archive: compressed trajectory and raw bounded history for audit/reconstruction.
- Recall at Agent initialization / before first LLM step, format into a new `<memory_context>` block in state prompt. Agent should treat shortcuts/cache as candidates, verify current page/source before final reporting, and avoid stale-cache fabrication.
- Write through lifecycle callbacks, not model-visible memory tools initially, to avoid burdening the browsing model and to capture every tool-heavy trajectory.
- Final handoff in `browser-use_memory/memory_design.md` covers:
  - Agent summary and retention window.
  - Five gaps: repeated navigation, uncached obtained facts, noisy raw tool traces, site friction not learned, interrupted long tasks not resumable.
  - Five memory types: Navigation Shortcut Memory, Obtained Information Cache, Site Friction/Recovery Memory, Task Progress Snapshot, Session Trace Archive.
  - Write/recall flow and explicit gap coverage.
- Implementation emphasis for next phase: hook into `register_done_callback` and startup context assembly; do not replay raw indexes blindly; use stable semantic anchors and source-grounded cache entries with freshness policy.
- Implemented as opt-in memory:
  - `Agent(memory=True)` uses Browser-Use config dir `/memory`.
  - `Agent(memory_path=...)` uses a caller-provided local directory.
  - `BROWSER_USE_MEMORY_DIR` enables memory by environment.
  - Existing `run.py` remains memory-disabled unless the caller changes it.
- Memory package files:
  - `browser_use/memory/views.py`: Pydantic records.
  - `browser_use/memory/store.py`: SQLite persistence and dedup.
  - `browser_use/memory/service.py`: recall, prompt formatting, rigorous extraction, LLM extraction, flush/window commit.
  - `browser_use/memory/__init__.py`: public imports.
- Agent integration files:
  - `browser_use/agent/service.py`: memory setup, recall before each step, per-step writes, run-end commit.
  - `browser_use/agent/prompts.py`: injects `<memory_context>` before `<browser_state>`.
  - `browser_use/agent/message_manager/service.py`: passes memory_context through.
- LLM extraction detail: first tries provider structured output for a small `LLMMemoryDraft`; if OpenRouter model does not support schema response, retries with the same real provider using a JSON-only prompt and converts the draft into full memory records locally. This replaced the first full-schema extraction attempt because the provider produced malformed JSON for the large nested schema.
- Tests added in `tests/ci/test_memory_system.py`; real LLM test uses `.env` `OPENROUTER_API_KEY` and `OPENROUTER_MODEL`.

## Known Weaknesses
- [things that might break, edge cases not yet handled, concerns]
- Element indexes are page-state local and unstable; shortcuts must store semantic anchors and action intents, not only raw indexes.
- Cached web facts can become stale. Cache entries need source URL, observed_at, confidence, and freshness/TTL metadata.
- Login/session-specific paths may leak private workflow details; memory must be scoped by profile/session/user and redact sensitive action params.
- OpenRouter live run shows GitHub page readiness timeout even though task succeeds; shortcut recall must not assume a page has loaded solely from navigation success.
- Cross-session memory injection can bias the model into reporting unverified cached facts. Design must explicitly require verification before done.
- Implementation limitations to remember for refine:
  - Retrieval is lexical/domain scoring, no embeddings.
  - Warning logs that do not appear in serialized `ActionResult.error` are not captured as site friction.
  - `memory_profile_scope` exists as API shape but does not partition the SQLite records yet.
  - Freshness policy is heuristic.

## Open Questions
- [things I'm unsure about, need to decide afterwards]
- Exact storage backend is implement-phase: SQLite is likely sufficient; embeddings can be optional depending dependency appetite.
- Whether memory should be enabled by default or opt-in via Agent(memory=...) / Browser Use Cloud config.
- Whether cached facts should be reused directly for non-volatile queries (e.g. repo license) or always verified; design leans to "answer from cache only when task permits and cache freshness policy passes, otherwise use it as shortcut".

## Runtime Configs
- Repo uses uv. pyproject says Python >=3.11,<4.0, project scripts include browser-use/bu/browser and legacy browser-use-tui.
- AGENTS says default/recommend ChatBrowserUse for model choice; do not replace model names in code.
- User indicated OpenRouter model is in `.env`; `run.py` is the intended runner.
- Running `uv run python run.py` works in this checkout; direct `uv run browser-use --help` failed because the installed console script could not import local `browser_use`, but `uv run python - <<... import browser_use` works.
- Functional test command that passed after final LLM-draft fix: `PYTHONPATH=. uv run python -m pytest tests/ci/test_memory_system.py -q` (5 passed, includes real OpenRouter extraction).
- Compile command that passed: `PYTHONPATH=. uv run python -m compileall -q browser_use/memory browser_use/agent/prompts.py browser_use/agent/message_manager/service.py browser_use/agent/service.py`.
- Post-integration `uv run python run.py` passed, task completed successfully.
- Memory-enabled inline agent run passed with `memory_path=/tmp...`, `memory_use_llm=False`; committed 1 shortcut, 1 cached fact, 0 site notes, and archived trace.

## Refine Summaries
<!-- Appended at the end of refine session. Read-only during refine. -->

### Refine Loop Closeout - 2026-05-11
- Ran the Critic-Improver refine loop through Phase 5. Phase 4 Critic was relaunched after the crash and recovered the checkpoint by inspecting existing traces/results before writing `refine_state.json`.
- Phase 5 was configured as the final phase (`current_phase=5`, `max_phases=5`). The Improver fixed the Phase 4 weaknesses around noisy consolidation, same-domain site-friction ranking, and exact task/session resume matching.
- Phase 5 Critic validated the state, reran the active suite, and all existing active traces passed. It then added three escalation traces: `trace_id_session_consolidation_superseded_source`, `trace_id_site_friction_blocked_variants`, and `trace_id_task_progress_middle_milestone_retention`.
- Final scores on new Phase 5 escalations exposed remaining weaknesses: session consolidation `support_coverage=0.6`, `query_success=0.2`; site friction `hit_rate=0.6`; task progress `support_coverage=0.6667`, `query_success=0.4`. The e2e trace stayed at `support_coverage=1.0`, `query_success=1.0`.
- Outcome: Critic set `stop_decision.decision=stop` because max phases were reached. No paths graduated; final `step_status` is `critic_finished`, and the final state validator passed.

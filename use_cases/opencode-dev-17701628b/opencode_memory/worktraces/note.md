# note.md

Your persistent working memory for this pipeline. Write anything here that
you need to remember across steps — there are no formatting rules, required headers, 
and no one grades this file. Optimize it for your own future use. This may include:

---

## User Requirements
- User invoked `/auto-memory-design /data2/junzhou2/opencode`.
- Need explore opencode deeply, identify existing memory/retention behavior, consult Auto_Memory reference patterns, then write `/data2/junzhou2/opencode/opencode-memory/memory_design.md`.

## Checkpoints
- [x] Create required working note.
- [x] Read README, manifests, entry points, config.
- [x] Identify current memory/session/context persistence and retention window mechanics.
- [x] Run or inspect agent lifecycle on simple task if feasible.
- [x] Consult reference memory pattern catalog.
- [x] Write complete memory design and update notes.

## Agent Observations
- OpenCode is a client/server AI coding agent with CLI/TUI/web clients. CLI entry point: `packages/opencode/src/index.ts`.
- Main package is Bun/TypeScript. `opencode run` starts local server/bootstrap, creates a session, streams JSON/text events, and persists session/message/part rows.
- Smoke run succeeded after initial stall/kill timing: `opencode run --pure --format json --title "memory design smoke test" "Reply with exactly: smoke-ok"` created `ses_232fc5f36ffeDk7Z4ZuLqgZ4sp`, returned `smoke-ok`, and persisted user/assistant messages with step-start, reasoning, text, step-finish, token/cost metadata.
- Default observed model/provider in local env: `openrouter` / `minimax/minimax-m2.7`.

## Discoveries
- Persistence schema: `packages/opencode/src/session/session.sql.ts` stores `session`, `message`, `part`, `todo`, and `session_entry`.
- Session rows include project/workspace/parent/title/version/permission/archive/compacting metadata. Message rows store info JSON; part rows store text/file/tool/compaction/subtask/etc JSON.
- Main loop: `packages/opencode/src/session/prompt.ts` loads `MessageV2.filterCompactedEffect(sessionID)`, resolves agent/tools/system/skills/environment/instructions, converts messages with `MessageV2.toModelMessagesEffect`, then streams via `SessionProcessor`.
- No first-class long-term memory subsystem found. Existing memory-like mechanisms are session history, compaction summaries, tool-output pruning, local UI persistence, skills, and plugin hooks.
- Plugin hooks relevant to memory integration: `experimental.chat.messages.transform`, `experimental.session.compacting`, and `experimental.compaction.autocontinue`.

## Retention Window Mechanics
- Active model-visible history is a sliding compacted view, not the full SQLite history.
- Overflow threshold: `session/overflow.ts` computes usable tokens from model context/input limit minus reserved compaction buffer. Default reserved buffer is `min(20_000, maxOutputTokens(model))`; auto compaction can be disabled with `compaction.auto=false`.
- After a non-summary assistant finishes, `prompt.ts` schedules compaction if last assistant token count is >= usable threshold. Provider context overflow also schedules compaction via `SessionProcessor`.
- Compaction defaults: `DEFAULT_TAIL_TURNS=2`; recent-tail budget defaults to 25% of usable tokens clamped between 2,000 and 8,000 tokens. Older head is summarized; selected tail remains verbatim.
- `filterCompacted` keeps the completed summary and messages from `tail_start_id` forward in the model input. Older rows still exist in SQLite unless tool output pruning clears content.
- Tool pruning: if `compaction.prune` is enabled, older completed tool outputs after the last two turns can be marked compacted once prunable estimate exceeds 20,000 tokens, while protecting 40,000 recent tool-output tokens and protected tools like `skill`.
- UI/session-list trimming is separate from model context: recent window 4h, recent limit 50; layout local persisted state prunes to 50 session keys.

## Design Decisions
- Weight class: medium. OpenCode has typed sessions, SQLite persistence, sync events, and plugin hooks, but interactive latency argues against heavy multi-agent/vector-only memory.
- Memory types in final design:
  1. Stable user/project facts: user-owned, scoped, small always-in-context block; explicit agent writes plus compaction/session-stop extraction.
  2. Active task state and handoff: project/workspace current-state summary with replacement compression; recalled on resume/new related session.
  3. Reusable agent experience and cases: agent-owned tool/session observations with L0/L1/full evidence pointers; retrieved by project/files/commands/errors/prompt keywords.
  4. Tool and skill operational memory: deterministic stats plus merged guidelines; recalled around tool selection/errors.
  5. Session trajectory archive: searchable session cards over persisted sessions; compact related prior sessions injected or fetched on demand.
- Governance: separate user vs agent ownership, scope every record, keep evidence pointers/status, support correction/deletion, redact secrets/private content.
- Recall pipeline: always-on stable facts + active task state; selective retrieval for past experience/tool lessons/session cards; progressive disclosure to stay under budget.
- Write pipeline: lifecycle-triggered writes on user prompt, tool completion/error, compaction, session stop/end, and before pruning; agent-triggered writes for explicit remember and high-confidence lessons.

## Known Weaknesses
- Plugin-first implementation may not have enough control over prompt injection, pruning coordination, and deletion UX.
- Keyword/metadata retrieval may miss paraphrased prior sessions; embeddings can be added later if needed.
- Task handoff can become stale if completion/unrelated-task detection is weak.
- Overremembering inferred preferences is risky; extraction should be conservative and confidence-scoped.

## Open Questions
- Where does opencode assemble model context and trim/compact session history?
- What artifacts persist after a session, and are they scoped by project/user/session?
- Can a local/simple run be performed without external API credentials?
- Resolved: context assembly is in `session/prompt.ts`; active history is `MessageV2.filterCompactedEffect`; persisted artifacts are SQLite session/message/part rows plus UI-local state. Simple run worked using local OpenRouter config.
- Remaining: core service vs external plugin first; exact memory UX; whether first retrieval should use embeddings; exact cross-project scope semantics.

## Runtime Configs
- Help: from `packages/opencode`, `bun run --conditions=browser src/index.ts --help`.
- Run: `bun run --conditions=browser src/index.ts --pure run --format json --title "..." "message"`.
- Session list: `bun run --conditions=browser src/index.ts session list --format json --max-count N`.
- Export: `bun run --conditions=browser src/index.ts export <sessionID>`.

## Refine Summaries
<!-- Appended at the end of refine session. Read-only during refine. -->

---

## Implementation (auto-memory-implement)

- Implemented decoupled memory in `packages/opencode/src/memory/` (facts, observations + hash dedupe, handoff JSON, tool_stats JSON, sessions.jsonl, recall, redact, session-archive). No imports from session/agent internals inside `memory/`.
- Bridge: `packages/opencode/src/session/agent-memory.ts` (`AgentMemory` service): recall strings, explicit remember/forget, tool-settled metrics + optional observation LLM extract, post-compaction extraction (facts/obs/handoff), handoff strings for compaction context. `noopAgentMemoryLayer` for tests.
- Wired: `prompt.ts` (recall + user ingest + layer), `compaction.ts` (handoff context + afterCompaction + runtime layer merge), `processor.ts` (onToolSettled), `session.ts` (archive → `persistSessionTrajectory`), `tool/memory.ts` + registry (`memory_recall`), `config.ts` (`memory.enabled`, `memory_model`).
- Persistence root: `<sessionDirectory>/.opencode/memory/`.
- Tests: `packages/opencode/test/memory/memory-system.test.ts` — FS round-trips, dedupe, lifecycle, recall keyword match, redaction, session trajectory; OpenRouter `generateObject` slice when key is present (stashed in `test/preload.ts` before env clear). Model override: `OPENCODE_MEMORY_LLM_TEST_MODEL` (default `minimax/minimax-m2.7` for regional availability).
- Handoff: `opencode-memory/implemented.md`, `worktraces/refine_state.json` (`step_status: ready`).



---

## Refine (auto-memory-refine)

- Completed Critic-Improver refine loop through phase 7/7.
- Final state: `step_status=critic_finished`, `stop_decision=stop`; exit reason was max phases reached.
- Final Critic checkpoint recorded 38 trace scores; 33 traces/checks were at full score, and `e2e` remained at 1.0.
- Graduated paths: none.
- Major improvements made across phases: fact correction/forget normalization, observation duplicate calibration, action-prioritized handoff formatting, richer tool error lessons, session-card replacement/redaction, and tail-preserving session archives.
- Remaining Critic weaknesses at stop:
  - `fact_lifecycle` (high): Natural forget handling still relies on brittle substring matching and topic-specific fuzzy expansions, so a paraphrased request to forget the data retention rule for customer exports leaves the corrected 30-month customer export retention fact active.
  - `observations_store` (high): Observation near-duplicate detection misses semantically duplicate lessons when wording overlap is low and no code-like tokens are shared, so the same Prisma shadow-database failure/remediation is stored twice.
  - `handoff_state` (medium): Compact handoff formatting caps mid-budget output at four next steps even when space remains, dropping additional critical resume actions such as notifying the payment owner.
  - `tool_operational_memory` (medium): Tool lesson formatting reports at most three common error samples, so a fourth repeated high-value failure signature is omitted even under a roomy single-tool budget.
  - `session_trajectory_archive` (medium): Production trajectory serialization preserves only prefix and suffix excerpts of an oversized final assistant message, so important final outcomes located in the middle of that message are omitted from the archived session card.

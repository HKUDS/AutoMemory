# Memory Design: opencode

## Agent Summary

OpenCode is a Bun/TypeScript AI coding agent with a client/server architecture. The main CLI entry point is `packages/opencode/src/index.ts`; the `run`, TUI, web, ACP, and HTTP clients all work through the server/session services. Sessions are persisted in SQLite through `session`, `message`, `part`, `todo`, and `session_entry` tables. Message and part updates are projected through the sync event system, so a turn is durably represented as message metadata plus typed parts such as text, reasoning, tool calls, files, compaction markers, and step boundaries.

The core agent loop is in `packages/opencode/src/session/prompt.ts`. Each loop loads the active thread with `MessageV2.filterCompactedEffect(sessionID)`, resolves the selected agent, tools, skills, environment, and instruction files, converts session parts into model messages with `MessageV2.toModelMessagesEffect`, then streams the model response through `SessionProcessor`.

OpenCode already has session-local memory, but not long-term memory. Existing memory-like behavior is:

- Full session/message/part persistence in SQLite.
- Automatic compaction summaries when context is near or over the provider limit.
- Optional tool-output pruning that clears old tool result bodies.
- Instruction and skill injection from configured files.
- Plugin hooks that can transform model messages or add compaction context.
- UI-local persistence and session-list trimming, separate from model context.

Retention window: the model does not always see the full persisted session. Overflow is detected from the last assistant token count against the usable model window. Usable context is the model context or input limit minus reserved output/compaction space; the default reserved buffer is `min(20_000, maxOutputTokens(model))`. If `compaction.auto` is not disabled and the count exceeds the usable threshold, the loop creates a compaction task. Compaction keeps a recent tail, defaulting to 2 user turns, within a budget equal to 25% of usable context clamped between 2,000 and 8,000 tokens. Older head history is summarized by a compaction agent. After a completed compaction, `filterCompacted` supplies the summary plus the retained tail and newer messages to the model. Older rows remain in SQLite, but they are no longer directly visible unless deliberately retrieved. Tool-output pruning can also mark old completed tool outputs as compacted after preserving 40,000 recent tool-output tokens and finding more than 20,000 prunable tokens; compacted tool outputs become `"[Old tool result content cleared]"`.

A smoke run confirmed the lifecycle in this environment: `opencode run --pure --format json --title "memory design smoke test" "Reply with exactly: smoke-ok"` created a persisted session, streamed step/text/finish JSON events, and exported user and assistant messages with token and cost metadata.

Reference patterns consulted: lifecycle hooks, tool-observation extraction, token-pressure consolidation, state compression, dual user/agent ownership, tiered L0/L1/L2 representations, grep retrieval, and progressive disclosure.

## Gap Analysis

### Gap 1: Cross-session user and project facts are not captured

Information lost: stable user preferences, project constraints, environment quirks, and explicit "remember this" statements that appear in chat but are not manually added to `AGENTS.md`, config, or skills.

When loss happens: at session boundary, when starting a new unrelated session, and during compaction if the fact is summarized weakly or omitted.

Impact: the agent asks repeated questions, violates previously stated preferences, or rediscovers project-specific constraints.

Root cause: missing user/project-scoped memory extraction and recall. Current instructions are file-based and deliberate; chat-derived stable facts are not promoted into reusable context.

### Gap 2: Task state is session-local

Information lost: current plan, partial progress, unresolved blockers, important decisions, and "next step" context when the user starts a new session instead of continuing/forking the previous one.

When loss happens: session boundary, UI session trimming, user switching clients, or long-running work being resumed from a different entry point.

Impact: the agent restarts exploration, repeats prior work, or misses open commitments.

Root cause: task state exists in session messages, todos, and compaction summaries, but there is no project-level active handoff memory recalled at the beginning of related sessions.

### Gap 3: Compaction summaries are optimized for continuity, not reusable memory

Information lost or mishandled: command outcomes, exact errors, file-specific discoveries, tool outputs, and successful/failed procedures that are too detailed for the compaction summary but valuable later.

When loss happens: context overflow compaction and old tool-output pruning.

Impact: the agent loses evidence needed to avoid repeating failed commands, re-run expensive searches, or remember why a decision was made.

Root cause: representation and timing issue. The compaction prompt creates one continuity summary while content is leaving the active context, but no separate extraction captures durable observations before or during pruning.

### Gap 4: Reusable tool and skill experience is not learned

Information lost: which tools work well for which tasks, common command failures in this environment, provider/model quirks, permission patterns, slow or noisy commands, and successful tool sequences.

When loss happens: after tool completion, session end, and pruning.

Impact: the agent repeats brittle commands, chooses tools without local performance knowledge, and cannot accumulate operational expertise across sessions.

Root cause: missing capability. Tool parts are persisted but not aggregated into cross-session statistics or guidelines.

### Gap 5: Past sessions are hard to retrieve by relevance

Information lost or inaccessible: older session outcomes, file-specific history, prior investigations, and decisions not in the active thread.

When loss happens: practically at recall time. Data may remain in SQLite, but the model has no relevance search/index and no compact catalog of past sessions.

Impact: useful previous work is invisible unless the user manually names the exact session or context.

Root cause: missing retrieval layer. Current APIs list/export sessions, but they are not formatted as memory candidates or injected by relevance.

### Gap 6: User facts and agent-learned experience are mixed together

Information mishandled: user preferences/profile data, project facts, agent observations, tool statistics, and task handoffs can all appear in the same conversation summaries.

When loss happens: extraction or summarization time.

Impact: the agent may overgeneralize one task's workaround as a user preference, or inject private/user-scoped data where only agent operational memory is appropriate.

Root cause: missing ownership and scope model.

## Design Decisions

### Weight Class

Use a medium-weight memory system.

Rationale: OpenCode is a tool-heavy coding agent with durable SQLite state, typed message parts, project/workspace identity, and plugin/sync-event surfaces. It needs more than a flat always-in-context file, because reusable knowledge spans tools, files, sessions, and projects. It should not start with a heavy multi-agent/vector-only architecture, because memory must fit an interactive coding loop and should not add multiple LLM calls to every turn.

Budget target: normal turns should add no more than one retrieval pass and no mandatory extraction LLM call. Writes can run asynchronously after tool completion, compaction, and session stop. Recall should inject a small always-on block plus a few high-confidence relevant items, typically under 2,500 tokens total. Larger details should be fetched only on demand.

### Memory Type 1: Stable User and Project Facts

Purpose: remember stable information that should influence future behavior: user preferences, project conventions, environment constraints, recurring instructions, important repo facts, and explicit "remember" requests.

Write actor: both agent-triggered and system-triggered. The agent should write immediately when the user explicitly asks to remember something. The system should also extract candidates from user messages and compaction/session-stop summaries.

Write timing: while the relevant turn is still in the active window for explicit memory; at compaction and session stop for implicit candidates.

Write method: rigorous for explicit commands and obvious preference patterns; intelligent LLM extraction for implicit facts. Extraction must classify scope as user-global, project, workspace, or session-derived.

Storage form: concise structured records with owner `user`, scope, title, fact text, evidence pointers to session/message/part ids, confidence, timestamps, and status. A compact merged profile/preferences view should be maintained separately from append-only evidence.

Update semantics: merge compatible facts; overwrite only when the user gives a clear replacement; mark contradicted facts inactive rather than deleting evidence. Project facts should not become global without explicit user intent.

Recall timing: at session creation and before each prompt assembly when the active project or user query changes materially.

Recall method: always include a small stable fact block for the current project/user, capped by budget. Prefer exact project/workspace matches, then user-global facts. Do not include low-confidence or stale facts unless the current prompt asks about them.

Injection point: system context alongside environment/instructions, clearly labeled as remembered user/project facts and lower priority than the latest user message.

Budget fit: keep the always-in-context view under roughly 1,000 tokens. Store detailed evidence out of context.

### Memory Type 2: Active Task State and Handoff

Purpose: preserve plans, partial progress, blockers, decisions, files touched, and next steps across session boundaries and compactions.

Write actor: system-triggered, with optional agent-triggered updates when the plan changes substantially.

Write timing: after session stop/idle, after compaction succeeds, after todo updates, and after assistant messages that finish a meaningful phase of work.

Write method: intelligent state compression with replacement semantics. The current task state should be updated from the previous state plus new trajectory, not appended indefinitely.

Storage form: one active handoff state per project/workspace plus optional session-linked historical states. Fields should include goal, constraints, done, in progress, blockers, decisions, next steps, relevant files, evidence ids, and freshness.

Update semantics: replace the current active state when newer evidence supersedes it; archive previous states as history. Clear or mark complete when the user ends the task or a new unrelated goal is detected.

Recall timing: at new session start, continue/fork, and prompt assembly when the prompt appears related to the active project/task.

Recall method: inject the current active handoff when the project matches and either the task is recent, the user says to continue/resume, or the prompt overlaps with files/goals in the handoff.

Injection point: system context after stable facts, labeled as recent task state. During compaction, include it as extra compaction context so the summary does not drop open commitments.

Budget fit: target 500-900 tokens. Use replacement compression so it stays bounded.

### Memory Type 3: Reusable Agent Experience and Cases

Purpose: remember useful prior experience: successful procedures, failure patterns, exact error resolutions, repo-specific discoveries, command outcomes, and implementation decisions that may help future tasks.

Write actor: system-triggered from completed tool parts and assistant/session-stop summaries; agent-triggered when the agent identifies a reusable lesson.

Write timing: after tool completion, after tool errors, after session stop, and before tool-output pruning or compaction can erase details.

Write method: intelligent tool-observation extraction. The extractor should prefer tool I/O and final outcomes over raw dialogue. It should produce observations only when future reuse is plausible.

Storage form: structured observations with owner `agent`, scope, type (`case`, `pattern`, `environment_quirk`, `decision`, `failure`, `procedure`), title, narrative, facts, commands, tool names, files read/modified, error strings, applicability, outcome, evidence ids, and token-cost estimate. Store a short L0 abstract and a richer L1 overview; full details can point back to persisted session/part data while available.

Update semantics: create immutable cases for one-off events; merge repeated cases into patterns when similar observations recur; deduplicate by evidence hash plus near-duplicate title/narrative matching.

Recall timing: before prompt assembly for new user requests, and after tool errors when a matching error/command/file appears.

Recall method: generate candidates by project scope, mentioned files, commands, tool names, exact error strings, and keywords from the user prompt. Optionally add semantic retrieval later, but keyword/metadata retrieval should be sufficient for the first version. Use progressive disclosure: inject only L0/L1 for top candidates, and allow full detail retrieval if the agent needs it.

Injection point: a "Relevant Past Experience" block in system context, or an on-demand memory tool result if the agent asks for more detail.

Budget fit: inject at most 3-5 observations, with each L0/L1 excerpt capped. Full historical detail should not be injected automatically.

### Memory Type 4: Tool and Skill Operational Memory

Purpose: accumulate operational knowledge about OpenCode tools and configured skills: success rates, common failures, best parameters, expensive commands, noisy outputs, permission issues, and recommended flows.

Write actor: system-triggered from message part updates for tool calls and skill invocations.

Write timing: immediately after each tool reaches completed or error status, with periodic asynchronous consolidation.

Write method: rigorous metrics plus intelligent guideline merging. Counts, durations, token/output sizes, and statuses should be computed deterministically. Guidelines such as "avoid this command in this repo" can be LLM-merged from repeated evidence.

Storage form: per-tool/per-skill records with call count, success/error counts, average duration, typical output size, common failure signatures, useful parameters, recommended flow, and evidence samples.

Update semantics: counters update deterministically; guideline text is merged only when there is enough evidence or a high-confidence single failure. Decay or down-rank stale observations.

Recall timing: when resolving tools for an agent, before a tool-heavy task, and after a related tool error.

Recall method: include only lessons for tools available to the current agent and relevant to the current project/task. Prefer exact tool and command/error matches.

Injection point: either append compact lessons to tool descriptions or inject a short "Tool Lessons" context block before the model chooses tools.

Budget fit: no more than 300-600 tokens per turn. Most metrics remain out of context.

### Memory Type 5: Session Trajectory Archive

Purpose: make past sessions discoverable without replaying full conversations.

Write actor: system-triggered.

Write timing: at session stop/end, after compaction, and when a session title/summary changes.

Write method: use existing session metadata and compaction/session summaries where available; run one lightweight extraction when a session lacks a usable summary.

Storage form: append-only session cards with session id, project/workspace, title, time range, goal, outcome, files, commands/tools, concepts, failures, decisions, and links to message/part evidence. Each card should have L0 abstract and L1 overview; full data remains in the session store/export.

Update semantics: append timeline entries for important phases; update the session card when the session summary changes; preserve older entries for auditability.

Recall timing: at new session start, when the prompt references prior work, and on explicit "what did we do before" queries.

Recall method: keyword/metadata search over titles, files, concepts, commands, and timestamps. Present compact candidates first; fetch full session details only on demand.

Injection point: for automatic recall, inject a short "Related Prior Sessions" block. For explicit queries, return search/timeline results as normal assistant context.

Budget fit: automatic injection should normally include only 1-3 session cards.

### Memory Governance

All memory records need ownership and scope: `user` versus `agent`, plus global/project/workspace/session applicability. User-owned memory must not be mixed into agent operational patterns. Agent-owned operational lessons must not be phrased as user preferences.

Each record should retain evidence pointers and status. Updates should support active, superseded, rejected, and deleted/inactive states. The user must be able to ask what is remembered and request deletion or correction. Retrieval should exclude deleted/inactive memories by default.

Privacy rule: do not store secrets, credentials, raw environment variables, or private content marked by the user. Tool-output extraction should redact likely secrets before memory writes.

### Recall Pipeline

Use a two-tier recall pipeline:

1. Always-on small context: stable facts for current scope plus active task state.
2. Selective recall: retrieve relevant agent experience, tool lessons, and prior session cards using project, file, tool, command, error, and prompt keywords.

The first implementation should prefer deterministic filters and keyword search because OpenCode already has rich typed metadata in message parts. Embeddings or rerankers can be added later if keyword recall misses paraphrased queries.

Recall formatting should make provenance and scope clear:

- `Remembered User/Project Facts`
- `Current Task State`
- `Relevant Past Experience`
- `Tool Lessons`
- `Related Prior Sessions`

The latest user message and explicit instruction files remain higher priority than memory. Memory should be advisory unless it is a user-owned fact or project convention.

### Write Pipeline

Use lifecycle-triggered writes so the agent does not have to remember to save memory manually:

- On user prompt: detect explicit remember/correction/delete requests.
- On tool completion/error: extract candidate observations and update tool metrics.
- On compaction: capture reusable facts and observations from the head before it leaves the active window.
- On session stop/end: update active task state and session trajectory card.
- On pruning: ensure tool outputs selected for pruning have either no memory value or have already produced observations.

Agent-triggered writes should still exist for explicit "remember this" and high-confidence lessons, but the reliable baseline should be system-triggered.

## Gap Coverage

### Gap 1: Cross-session user and project facts

Addressed by Stable User and Project Facts. Immediate agent-triggered writes capture explicit remember requests before context eviction. Session-stop and compaction extraction capture implicit stable facts. Scope and ownership prevent project-only facts from becoming global.

What could fall through: ambiguous preferences that are only implied once should stay low confidence or be omitted. This is acceptable; overremembering is worse than asking again.

### Gap 2: Task state is session-local

Addressed by Active Task State and Handoff. Session-stop, compaction, and todo-derived writes preserve the current goal and next steps in a bounded replacement summary. New sessions in the same project can recall it when the user resumes or prompt overlap is high.

What could fall through: unrelated new sessions in the same repo may receive stale handoff if task detection is too broad. Freshness, explicit completion, and prompt-overlap gating reduce this.

### Gap 3: Compaction summaries lose reusable detail

Addressed by Reusable Agent Experience, Session Trajectory Archive, and compaction-time writes. The system extracts tool observations and session cards while source messages still exist. Pruning should happen after memory extraction or skip valuable unprocessed outputs.

What could fall through: very large binary/media outputs may still be reduced to placeholders. The design stores metadata and evidence pointers, but not necessarily raw media.

### Gap 4: Tool and skill experience is not learned

Addressed by Tool and Skill Operational Memory. Deterministic metrics and merged guidelines turn repeated tool outcomes into compact advice recalled only when relevant.

What could fall through: rare one-off tool quirks may be stored as cases rather than generalized guidelines until they recur.

### Gap 5: Past sessions are hard to retrieve

Addressed by Session Trajectory Archive and selective recall. Session cards create a compact searchable layer over persisted sessions, with L0/L1 summaries for low-cost candidate injection and full details available on demand.

What could fall through: sessions with poor titles and no tool/file metadata may be hard to retrieve by keyword. A later semantic layer can improve this without changing the memory model.

### Gap 6: User facts and agent experience are mixed

Addressed by Memory Governance and the dual ownership model. Extraction must classify owner and scope, and recall formatting keeps user/project facts separate from agent operational lessons.

What could fall through: a statement can be both a user preference and a project convention. In that case, store two scoped records with shared evidence rather than one ambiguous record.

## Open Questions

- Should memory be implemented as a core OpenCode service or an external plugin first? The plugin hooks are promising for experimentation, but core integration would make prompt injection, pruning coordination, and deletion UX more reliable.
- Should the first retrieval version include embeddings, or start with SQLite/FTS-style keyword and metadata filters? The design recommends keyword/metadata first, with embeddings as an optional later improvement.
- What user interface should expose "what do you remember?", memory correction, and deletion?
- How should cross-project memory work for monorepos and nested worktrees? The design assumes project/workspace scope, but implementation should define exact scope boundaries.
- Should task handoff be automatically cleared on successful completion, or only when the user starts a clearly unrelated task?

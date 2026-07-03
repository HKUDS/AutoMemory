# Memory Implementation: opencode

## Memory Architecture

Long-term workspace memory lives on disk under each session working directory at `<sessionDirectory>/.opencode/memory/` (see `memoryDataDir`). The decoupled package `packages/opencode/src/memory/` owns all persistence and recall formatting. The agent imports this package only from the bridge `packages/opencode/src/session/agent-memory.ts` (`AgentMemory` Effect service), plus call sites in `prompt.ts`, `compaction.ts`, `processor.ts`, `session.ts`, and the `memory_recall` tool.

**Write paths**

- User explicit “remember …” / “memorize …” → `appendFact` (project-scoped user fact).
- User “forget …” / “delete memory …” → `setFactStatus` to `inactive` by title/text substring.
- Tool completed/error → `recordToolOutcome`; optional LLM `generateObject` extracts 0–2 observations → `appendObservation` (dedupe via `evidenceHash` + hash file).
- Successful compaction → transcript excerpt + new summary → LLM extraction → `appendFact`, `appendObservation`, optional `writeHandoff`.
- Session archived → `persistSessionTrajectory` → `appendSessionCard`.

**Read paths**

- Before each model call, `AgentMemory.recallSystemStrings` loads `buildMemoryContextBlocks` (facts, formatted handoff, ranked observations, tool lesson lines, related session cards) merged into system context in `prompt.ts`.
- Compaction receives extra strings from `handoffCompactionContext` so open commitments stay visible while summarizing.
- On demand, the `memory_recall` tool re-runs `buildMemoryContextBlocks` with a free-text query.

**Config**

- `memory.enabled` (default on): when `false`, all memory writes and recall injection no-op.
- `memory_model`: optional provider/model string for extraction calls; otherwise the session’s default chat model (via `Provider` + `Auth`) is used.

## Memory Implemented

| Area | Location |
|------|-----------|
| Core stores + recall | `packages/opencode/src/memory/` (`index.ts` re-exports) |
| Agent bridge | `packages/opencode/src/session/agent-memory.ts` |
| System recall injection | `packages/opencode/src/session/prompt.ts` |
| Compaction hooks | `packages/opencode/src/session/compaction.ts` |
| Tool metrics + observation extract | `packages/opencode/src/session/processor.ts` (`onToolSettled`) |
| Session archive on archive | `packages/opencode/src/session/session.ts` |
| Tool registration | `packages/opencode/src/tool/memory.ts`, `tool/registry.ts` |
| Config schema | `packages/opencode/src/config/config.ts` (`memory`, `memory_model`) |
| Functional tests | `packages/opencode/test/memory/memory-system.test.ts` |
| Test harness key stash (real LLM in CI when key present) | `packages/opencode/test/preload.ts` (`__OPENCODE_STASHED_OPENROUTER_API_KEY__`) |

Extraction uses the same `generateObject` / `streamObject` (OpenAI OAuth) patterns as the rest of the agent. Secrets are passed through `redactLikelySecrets` before persistence.

## Memory Type Coverage

| Design type | Implementation |
|-------------|----------------|
| Stable user/project facts | `facts.jsonl` via `appendFact` / `listActiveFacts` / `setFactStatus`; recall in “Remembered User/Project Facts” block |
| Active task handoff | `handoff.json` via `writeHandoff` / `readHandoff`; compaction context + recall “Current Task State” |
| Agent experience / cases | `observations.jsonl` + `observation_hashes.json`; LLM extraction on tools and compaction; keyword-ranked recall |
| Tool operational memory | `tool_stats.json`; deterministic counters + heuristic `guideline` when error rate high |
| Session trajectory archive | `sessions.jsonl` via `appendSessionCard` / `persistSessionTrajectory`; “Related Prior Sessions” in recall |

## Known Limitations

- **Retrieval**: Keyword/token overlap only (no embeddings, no SQLite FTS on cards). Paraphrased queries may miss relevant cards or observations.
- **Handoff lifecycle**: Handoff is replaced when compaction extraction returns a `handoff` object; there is no separate “task complete” detector beyond model judgment during extraction.
- **Tool guidelines**: No separate LLM merge pass for long-form guidelines—only deterministic stats and a short heuristic guideline when failure ratio is high.
- **Scope**: Facts default to `project` for explicit user remember; extraction assigns scopes the model returns—misclassification is possible.
- **Region / model**: OpenRouter model availability varies by region; tests use `OPENCODE_MEMORY_LLM_TEST_MODEL` (default `minimax/minimax-m2.7`) when `OPENROUTER_API_KEY` is set before the test preload.
- **Session-derived facts**: `session`-scoped facts are filtered out of the default recall block (`formatFactsBlock`).

## Memory Components

### Component: paths / memory root

- `purpose`: Resolve on-disk root for all memory files for a workspace session directory.
- `write_method`: none (derived path only).
- `write_trigger`: none
- `read_method`: `import { memoryDataDir } from "@/memory/paths"; memoryDataDir(sessionDirectory)` returns `string` (absolute path under `.opencode/memory`).
- `read_returns`: filesystem directory path string.
- `eval_mode`: qa
- `persistence`: `<sessionDirectory>/.opencode/memory/`
- `requires_flush_window`: false
- `dependencies`: none
- `notes`: `sessionDirectory` is the OpenCode session’s project working directory from `Session.Service`.

### Component: facts (user/project stable facts)

- `purpose`: Append-only JSONL of scoped facts with status for soft delete.
- `write_method`: `import { appendFact } from "@/memory"; appendFact(sessionDirectory, { owner, scope, title, text, confidence, status, evidence })`
- `write_trigger`: agent_tool_call | system_trigger (compaction extraction, explicit remember in `AgentMemory.ingestUserExplicitRemember`)
- `read_method`: `import { listActiveFacts } from "@/memory"; listActiveFacts(sessionDirectory, limit?)`
- `read_returns`: `FactRecord[]` (active only in list; see types for fields).
- `eval_mode`: hit_rate
- `persistence`: `<memoryDataDir>/facts.jsonl`
- `requires_flush_window`: false
- `dependencies`: none
- `notes`: Updates use new append rows; `setFactStatus` marks older rows inactive without deleting file history.

### Component: fact status (forget / correct)

- `purpose`: Mark matching facts inactive by predicate over parsed JSONL.
- `write_method`: `import { setFactStatus } from "@/memory"; setFactStatus(sessionDirectory, predicate, "inactive")` where `predicate` is `(f: FactRecord) => boolean`.
- `write_trigger`: user_manual (phrases routed in `AgentMemory.ingestUserForgetOrCorrect`)
- `read_method`: `listActiveFacts` (inactive excluded)
- `read_returns`: filtered facts
- `eval_mode`: qa
- `persistence`: same `facts.jsonl`
- `requires_flush_window`: false
- `dependencies`: facts component
- `notes`: Substring match on title/text lowercased from user “forget …” tail.

### Component: observations (agent experience)

- `purpose`: Store deduplicated L0/L1 observation records keyed by `evidenceHash` and a sidecar hash set.
- `write_method`: `import { appendObservation } from "@/memory"; appendObservation(sessionDirectory, { owner: "agent", type, title, l0, l1, commands, tools, files, evidence, evidenceHash, ... })` — returns `ObservationRecord | undefined` if duplicate hash.
- `write_trigger`: system_trigger (tool settle + compaction extraction)
- `read_method`: `import { listObservations } from "@/memory"; listObservations(sessionDirectory, limit?)` for raw list; ranked injection is internal to `buildMemoryContextBlocks`.
- `read_returns`: `ObservationRecord[]`
- `eval_mode`: hit_rate
- `persistence`: `<memoryDataDir>/observations.jsonl`, `<memoryDataDir>/observation_hashes.json`
- `requires_flush_window`: false (writes triggered when transcript/tool output still available)
- `dependencies`: `hashEvidence` from `@/memory` or `@/memory/observations`
- `notes`: LLM-dependent fields; redaction applied in agent bridge before write.

### Component: handoff (active task state)

- `purpose`: Single JSON file replacing the latest task snapshot for the workspace.
- `write_method`: `import { writeHandoff } from "@/memory"; writeHandoff(sessionDirectory, HandoffRecord)`
- `write_trigger`: system_trigger (compaction extraction when model returns `handoff`)
- `read_method`: `import { readHandoff } from "@/memory"; readHandoff(sessionDirectory)` and `import { formatHandoffForPrompt } from "@/memory"; formatHandoffForPrompt(record, maxChars)`
- `read_returns`: `HandoffRecord | undefined`; formatted string for prompts.
- `eval_mode`: qa
- `persistence`: `<memoryDataDir>/handoff.json`
- `requires_flush_window`: false (compaction supplies “flushed” head as excerpt)
- `dependencies`: none
- `notes`: Full replacement write, not append-only history file.

### Component: tool statistics

- `purpose`: Per-tool counters and optional short guideline from failure rate heuristics.
- `write_method`: `import { recordToolOutcome } from "@/memory"; recordToolOutcome(sessionDirectory, { tool, ok, durationMs, error? })`
- `write_trigger`: system_trigger (`AgentMemory.onToolSettled` from processor)
- `read_method`: `import { readToolLessonsText } from "@/memory"; readToolLessonsText(sessionDirectory, availableToolIds, maxChars)`
- `read_returns`: single formatted string or empty string.
- `eval_mode`: qa
- `persistence`: `<memoryDataDir>/tool_stats.json`
- `requires_flush_window`: false
- `dependencies`: none
- `notes`: Deterministic merge; file is rewritten as whole JSON object.

### Component: session cards / trajectory archive

- `purpose`: Append-only session summary cards for keyword recall.
- `write_method`: `import { appendSessionCard, persistSessionTrajectory } from "@/memory";`  
  - `appendSessionCard(sessionDirectory, SessionCardRecord)`  
  - `persistSessionTrajectory({ session: ArchiveSessionMeta, messages: ArchiveMessage[] })`
- `write_trigger`: system_trigger (`persistSessionTrajectory` from `session.setArchived` path)
- `read_method`: `import { listSessionCards } from "@/memory"; listSessionCards(sessionDirectory, limit?)`
- `read_returns`: `SessionCardRecord[]`
- `eval_mode`: hit_rate
- `persistence`: `<memoryDataDir>/sessions.jsonl`
- `requires_flush_window`: false
- `dependencies`: `tokenizePrompt` / internal tokenization for concepts
- `notes`: Cards include `sessionID`; current session excluded in `buildMemoryContextBlocks` when `currentSessionID` set.

### Component: recall (composed context blocks)

- `purpose`: Build ordered, size-capped markdown blocks for system injection and tools.
- `write_method`: none
- `write_trigger`: none
- `read_method`: `import { buildMemoryContextBlocks } from "@/memory"; buildMemoryContextBlocks({ sessionDirectory, workspaceID?, userPrompt, currentSessionID?, availableToolIds?, maxTotalChars? })`
- `read_returns`: `string[]` — each element is one block (facts, handoff, past experience, tool lessons, prior sessions).
- `eval_mode`: hit_rate
- `persistence`: reads all store files above; no separate recall cache file.
- `requires_flush_window`: false
- `dependencies`: facts, handoff, observations, tool-stats, sessions
- `notes`: `maxTotalChars` default 9000; progressive truncation via `clampBlocks`.

### Component: redaction

- `purpose`: Strip likely secrets from strings before memory write or card serialization.
- `write_method`: none (pure function)
- `write_trigger`: none
- `read_method`: `import { redactLikelySecrets } from "@/memory"; redactLikelySecrets(text: string): string`
- `read_returns`: redacted string
- `eval_mode`: qa
- `persistence`: none
- `requires_flush_window`: false
- `dependencies`: none
- `notes`: Used in agent-memory and `session-archive` serialization.

### Component: AgentMemory (orchestration service)

- `purpose`: Effect `Context.Service` wiring config, session paths, provider language models, and all write/read entry points for the session loop.
- `write_method`: Not a single function—use `Effect.runPromise` / runtime with layer providing `AgentMemory`. Service methods: `ingestUserExplicitRemember`, `ingestUserForgetOrCorrect`, `onToolSettled`, `afterCompaction` (each `Effect` with `sessionID` + payloads as in `agent-memory.ts`).
- `write_trigger`: system_trigger | user_manual | agent_tool_call (indirect via processor/prompt)
- `read_method`: `recallSystemStrings({ sessionID, userPrompt, availableToolIds? })` → `Effect<string[]>`
- `read_returns`: array of memory blocks for system merging.
- `eval_mode`: qa | hit_rate
- `persistence`: delegates to components under session directory
- `requires_flush_window`: false
- `dependencies`: Config, Provider, Auth, Session, `@/memory` functions
- `notes`: Tests use `noopAgentMemoryLayer` from `@/session/agent-memory`. Default layer `defaultLayer` requires full app dependencies.

### Component: memory_recall tool

- `purpose`: On-demand recall for the model with a free-text query in the current session directory.
- `write_method`: none
- `write_trigger`: agent_tool_call
- `read_method`: Tool `memory_recall` with `{ query: string }` — implementation in `packages/opencode/src/tool/memory.ts` calling `buildMemoryContextBlocks`.
- `read_returns`: tool result object `{ title, output, metadata }` with concatenated blocks or “(No matching memory entries.)”
- `eval_mode`: qa
- `persistence`: same as recall inputs
- `requires_flush_window`: false
- `dependencies`: Session, `@/memory/recall`
- `notes`: Registered in `tool/registry.ts`.

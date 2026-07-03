# OpenCode Memory Use Case

This document summarizes the long-term memory added to OpenCode and the benchmark results from `opencode/bench`.

## Memory Integration Flow

OpenCode now keeps workspace-level memory under `<sessionDirectory>/.opencode/memory/`. The memory package owns persistence and recall formatting, while the session layer decides when to write and where to inject recalled context.

```text
                         +---------------------------+
                         |  OpenCode session loop    |
                         |  session/prompt.ts        |
                         +-------------+-------------+
                                       |
                    prompt assembly   |   user messages / tools / compaction / archive
                                       v
+---------------------------+   +------+--------------------+   +---------------------------+
| Recalled memory context   |<--| AgentMemory bridge        |-->| Workspace memory store    |
| injected into system text |   | session/agent-memory.ts   |   | .opencode/memory/        |
+---------------------------+   +------+--------------------+   +-------------+-------------+
                                       |                                      |
                                       |                                      |
                                       v                                      v
                         +-------------+-------------+        +---------------+--------------+
                         | Model/tool execution      |        | facts / handoff /            |
                         | processor + compaction    |        | observations / tool stats /  |
                         +---------------------------+        | session cards                |
                                                              +------------------------------+

High-level flow:
1. Before model calls, OpenCode reads relevant memory and injects compact blocks into system context.
2. During and after a turn, lifecycle events write facts, task handoff, observations, tool stats, and session cards.
3. Future sessions in the same workspace can reuse those memory records without replaying old transcripts.
```

## Memory Types

| Memory type | Storage | Write timing and method | Read timing and method | Token / size limit |
| --- | --- | --- | --- | --- |
| Stable user/project facts | `facts.jsonl` | Written on explicit user requests such as "remember"; also extracted after compaction. Forget/delete requests append inactive status updates through `setFactStatus`. | Read before each model call through `buildMemoryContextBlocks`, formatted as `Remembered User/Project Facts`. | Designed as a small always-on block, roughly under 1,000 tokens in the design. The implementation uses the shared recall budget. |
| Active task handoff | `handoff.json` | Written after successful compaction when extraction returns a handoff object. Replacement semantics keep one current task snapshot. | Read before model calls and during compaction via `readHandoff`, `formatHandoffForPrompt`, and `handoffCompactionContext`. | Target 500-900 tokens in the design. Formatted output is capped by recall/formatting character budgets. |
| Reusable agent experience / cases | `observations.jsonl`, `observation_hashes.json` | Written after tool completion/error and compaction. Tool-settled extraction asks the LLM for 0-2 useful observations, deduped by `evidenceHash`. | Read before model calls by keyword ranking inside `buildMemoryContextBlocks`; also available through `memory_recall`. | Inject only top relevant L0/L1 observations, originally designed as 3-5 compact items. |
| Tool operational memory | `tool_stats.json` | Written deterministically after every settled tool call through `recordToolOutcome`; high error rates can produce short heuristic guidance. | Read before model calls with `readToolLessonsText`, filtered to tools available to the current agent. | Designed for 300-600 tokens per turn; implementation returns a compact string within the shared recall cap. |
| Session trajectory archive | `sessions.jsonl` | Written when a session is archived through `persistSessionTrajectory`, using session metadata and transcript excerpts. | Read by `buildMemoryContextBlocks` as `Related Prior Sessions`, excluding the current session when known. | Automatic injection normally includes only 1-3 compact session cards. |
| On-demand recall | No separate store; reads all memory files | Not a write path. The model can call the registered `memory_recall` tool with a free-text query. | Calls `buildMemoryContextBlocks` and returns concatenated matching blocks. | `buildMemoryContextBlocks` defaults to `maxTotalChars = 9000`, then progressively clamps blocks. |

The implementation is controlled by `memory.enabled` and `memory_model`. When memory is disabled, reads and writes no-op. Memory extraction uses the normal OpenCode model/provider path, and likely secrets are redacted before persistence.

## Benchmark Setup

The benchmark scaffold in `opencode/bench` compares two conditions:

| Condition | Meaning |
| --- | --- |
| `memory-on` | OpenCode memory is enabled and sessions share `.opencode/memory/`. |
| `memory-off` | Memory is disabled through `opencode.jsonc`. |

The harness runs OpenCode on isolated per-session checkouts, writes prompts/logs/patches, and can run SWE-bench Docker verification. It records resolve status, token telemetry, tool calls, steps to first expected-file touch, and preference adherence signals such as `pytest -xvs` and `flake8`.

Two showcases were built:

| Showcase | Source tasks | Focus |
| --- | --- | --- |
| Showcase 1 | SWE-Bench-CL / SWE-bench Verified pytest sequence: six `pytest-dev/pytest` issues with two local dependency clusters. | Project-context accumulation and forward transfer across sequential issues in the same repository. |
| Showcase 2 | SWE-bench Verified `django/django`: `django__django-13964` split across sessions 1-2, plus related task `django__django-13933` in session 3. | Multi-session handoff continuity and user preference adherence. Session 1 asks OpenCode to remember test and lint preferences; session 2 continues the previous task. |

## Benchmark Results

### Showcase 1: Sequential Pytest Issues

| Condition | Resolved | Resolve rate | Total tokens | Tool calls |
| --- | ---: | ---: | ---: | ---: |
| memory-off | 5 / 6 | 0.833 | 2,087,028 | 110 |
| memory-on | 6 / 6 | 1.000 | 1,468,299 | 85 |

Memory improved the resolved count from 5 to 6 and reduced total tokens by about 30% and tool calls by about 23%. The decisive difference was session 6 (`pytest-dev__pytest-10051`). Both conditions identified the same root cause: `caplog.clear()` must not make `caplog.get_records("call")` point at a stale list. Memory-off patched `LogCaptureHandler.reset()` to clear `records` in place, but `reset()` is also used internally when pytest moves between setup/call/teardown capture phases. That broader change fixed the target behavior while breaking existing phase-capture behavior, so the verifier still failed `testing/logging/test_fixture.py::test_clear_for_call_stage` and one pass-to-pass test.

Memory-on had prior logging/caplog context available in shared memory, including notes about the stash-based phase records and the failed broader `reset()` approach. Its final patch kept `reset()` semantics intact and changed only `LogCaptureFixture.clear()` to clear the current handler records and stream. That narrower fix passed the fail-to-pass test and preserved all pass-to-pass logging fixture tests, so session 6 resolved only with memory enabled.

### Showcase 2: Multi-session Django Workflow

| Condition | Resolved | Resolve rate | Total tokens | Tool calls |
| --- | ---: | ---: | ---: | ---: |
| memory-off | 1 / 3 | 0.333 | 5,929,795 | 158 |
| memory-on | 1 / 3 | 0.333 | 623,231 | 38 |

The final SWE-bench resolve rate was equal in showcase2, but memory made the run much cheaper: total tokens dropped by about 89% and tool calls by about 76%. Session-level metrics show the same pattern: memory-on used fewer tokens and tool calls in all three sessions, and touched the expected file earlier in session 2 (`1` assistant step vs. `3` for memory-off).

The important qualitative result is session 2. In session 1, the user asked OpenCode to remember requirements such as running tests with `python -m pytest -xvs` and running `flake8` before submitting. With memory enabled, those requirements were persisted in shared memory (`facts.jsonl`) and session 2 carried them into the continuation. The measured preference signal shows `flake8: true` for memory-on session 2, while memory-off session 2 has `flake8: false`. This is the main showcase2 memory behavior: memory-on preserved a requirement introduced in session 1; memory-off did not. The `pytest -xvs` detector was false in both conditions, so the strongest measured preference-adherence claim is specifically about `flake8`.

Session 2 also shows handoff continuity. The memory-on patch directly removed the stale related-field value copy in `ForwardManyToOneDescriptor.__set__`, matching the bug location identified in session 1. The memory-off patch changed `ForeignKeyDeferredAttribute.__set__` instead. Both were unresolved by the SWE-bench verifier, but the memory-on trajectory better reflected the previous session's analysis.

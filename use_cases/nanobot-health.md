# Nanobot Health Memory Use Case

This document summarizes the health-state memory added to nanobot and the benchmark results from `nanobot-health/bench`.

## Memory Integration Flow

Nanobot already has a file-based memory architecture under the user workspace. The health memory extension keeps stable health facts in an always-loaded `HEALTH.md` profile and high-volume temporal events in `memory/health_log.jsonl`. The agent can write health facts immediately during conversation, while Dream and the Consolidator provide delayed backfill and precision-preserving archive paths.

```text
                                      +-------------------------------+
                                      |  Nanobot CLI / channel input  |
                                      |  MessageBus -> AgentLoop      |
                                      +---------------+---------------+
                                                      |
                                   user turns / tools | session messages
                                                      v
+-------------------------------+     +---------------+---------------+
| Live session transcript       |<--->| ContextBuilder + AgentRunner  |
| sessions/<key>.jsonl          |     | system prompt + LLM call      |
+---------------+---------------+     +---------------+---------------+
                |                                     |
                | token pressure                      | always-loaded bootstrap
                v                                     v
+---------------+---------------+     +---------------+---------------+
| Consolidator archive          |     | HEALTH.md                     |
| memory/history.jsonl          |     | stable health profile         |
+---------------+---------------+     +---------------+---------------+
                |                                     ^
                | Dream every ~2h or /dream           |
                v                                     |
+---------------+---------------+     +---------------+---------------+
| Dream health extraction       |---->| USER.md / SOUL.md / MEMORY.md |
| health-aware phase1/phase2    |     | general long-term files       |
+---------------+---------------+     +---------------+---------------+
                |
                | append temporal events
                v
+---------------+---------------+
| memory/health_log.jsonl       |
| symptoms / wellness / care    |
+---------------+---------------+

High-level flow:
1. Before each model call, `ContextBuilder.build_system_prompt` injects bootstrap files, including `HEALTH.md`, plus `MEMORY.md`, always-on skills, skill summaries, and recent unprocessed history.
2. During conversation, the agent uses file tools and the always-on `health-memory` skill to write stable profile facts to `HEALTH.md` and append temporal events to `memory/health_log.jsonl`.
3. When context pressure appears, the Consolidator archives older turns to `memory/history.jsonl` while preserving precise health values such as medications, doses, vitals, lab values, allergies, and symptom timing.
4. Dream periodically reads new history entries, extracts health-tagged facts/events, and updates `HEALTH.md` or `health_log.jsonl` as a secondary backfill path.
```

## Memory Types


| Memory type                     | Storage                                  | Write timing and method                                                                                                                                                                                                                                                                                                                              | Read timing and method                                                                                                                                                                                                                                   | Token / size limit                                                                                               |
| ------------------------------- | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Live conversation state         | `sessions/<key>.jsonl`                   | Written per turn by the existing session manager. CLI direct mode uses a fixed session key unless overridden by the bench.                                                                                                                                                                                                                           | Read while assembling recent conversation context for the active session.                                                                                                                                                                                | Bounded by nanobot's 65,536-token context window; old turns are snipped and archived when needed.                |
| General conversation archive    | `memory/history.jsonl`                   | Written by the `Consolidator` when token estimates exceed the retention threshold. The archive prompt was extended to preserve exact health measurements, medication doses, allergies, symptom timing, and lab values.                                                                                                                               | Read by Dream for delayed memory extraction and included as recent unprocessed history in the system prompt. It can also be searched with the agent's grep tool.                                                                                         | Append-only summaries. Keeps long-running conversation history outside the active prompt.                        |
| General long-term stable memory | `USER.md`, `SOUL.md`, `memory/MEMORY.md` | Written by Dream's existing two-phase process and by direct agent file edits. `USER.md` remains for non-health user facts and legacy health facts.                                                                                                                                                                                                   | Loaded by `ContextBuilder` as bootstrap/project memory on every model call.                                                                                                                                                                              | Always in context, so these files should stay compact and stable.                                                |
| Health Profile                  | `HEALTH.md`                              | Written immediately during conversation when the user shares stable or slowly changing health facts. Dream also writes `[HEALTH-PROFILE]` and `[HEALTH-GOAL]` backfill entries from history. Updates merge and deduplicate; corrections replace existing lines; discontinued meds and resolved problems are marked rather than silently deleted.     | Read on every model call because `HEALTH.md` is added to `ContextBuilder.BOOTSTRAP_FILES`. The agent uses it to answer profile questions and cross-reference symptoms against problems, medications, and allergies.                                      | Target roughly 1,500-2,500 tokens; realistic complex profiles should stay below about 3,000 tokens.              |
| Health Timeline                 | `memory/health_log.jsonl`                | Appended during conversation for symptoms, wellness logs, doctor visits, tests, immunizations, and care events. Dream also appends `[HEALTH-SYMPTOM]`, `[HEALTH-WELLNESS]`, and `[HEALTH-CARE]` entries from archived history. Entries include ISO timestamps, human-readable dates, category, and content that preserves the user's exact phrasing. | Read on demand through `search_health_log()` or grep-style recall when the user asks about health history, trends, dates, or symptom patterns. Recall normalizes natural-language dates and uses morphological term matching to reduce keyword mismatch. | Zero fixed prompt cost. Large daily logs stay out of the system prompt; only matching JSONL lines enter context. |
| Health behavior skill           | `skills/health-memory/SKILL.md`          | Created as an always-on skill. It encodes what counts as health data, where each category should be stored, how to format entries, when to search timeline logs, and how to avoid clinical overclaiming.                                                                                                                                             | Loaded as part of nanobot's always-on skills/skill summary during prompt assembly.                                                                                                                                                                       | Small always-on instruction block.                                                                               |
| Versioned memory store          | `.git/` under the workspace memory store | `GitStore` tracks long-term memory file changes, including the health profile.                                                                                                                                                                                                                                                                       | Used for auditability and change tracking rather than direct prompt recall.                                                                                                                                                                              | No prompt cost.                                                                                                  |


The health memory implementation is intentionally light: it adds structured markdown, append-only JSONL, prompt updates, and an always-on skill without introducing a database, embedding index, or extra runtime dependency beyond the existing Dream/LLM path.

## Benchmark Setup

The benchmark in `nanobot-health/bench` adapts an ESL-Bench health timeline into nanobot CLI interactions. Step-by-step data construction (timeline filtering, sparse QA, evaluation bundle) and nanobot bench runtime are documented in `[construction.md](nanobot-health-main-a2f4090e/bench/construction.md)`. It compares two conditions:


| Condition    | Meaning                                                                                                                                                                    |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `memory-on`  | Health memory enabled. Nanobot can persist `HEALTH.md`, append `memory/health_log.jsonl`, use health-aware Dream/Consolidator prompts, and recall health timeline entries. |
| `memory-off` | Original nanobot setting.                                                                                                                                                  |


The data construction flow starts from a structured profile and multi-year timeline, filters to a 2026 slice, keeps requirement-aligned health indicators, sparsifies daily device rows, and emits the evaluation bundle:

```text
profile.json
     |
     v
user_turn_profile.json ------------------+
                                         |
timeline.json -> 2026 slice -> sparse    +--> user_turn_mixed / user_turn.json
                                         |
                                         +--> question_sparse40.json
                                              120 QA items
```

Important bench details:


| Artifact / script                                                          | Role                                                                                                                                       |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `bench/ESL-Bench/data/evaluation/question_sparse40.json`                   | Canonical 120-question QA file: 20 profile-style questions plus 100 timeline questions, with sparse timeline ground truth.                 |
| `bench/ESL-Bench/data/evaluation/user_turn.json` or `user_turn_mixed.json` | Natural first-person ingest turns generated from profile and sparse timeline rows.                                                         |
| `bench/run_agent.py`                                                       | Replays every ingest user turn through `python -m nanobot agent -m ...` with a shared session/workspace.                                   |
| `bench/eval_agent.py`                                                      | Sends each QA question through nanobot on the same session and writes eval results.                                                        |
| `bench/run_esl_bench.py`                                                   | One-shot wrapper that runs ingest and eval with shared arguments.                                                                          |
| `bench/llm_judge_eval.py`                                                  | LLM judge scoring path. The result schema is `esl_eval_results_llm_judge_v1`; top-level `passed` and `pass_rate` follow the judge verdict. |


The run used here evaluates 120 questions at simulated time `2026-02-15T15:00:00`. The memory-on result is `bench/run_result/memory-on/eval_results.json`; the memory-off result is `bench/run_result/memory-off/eval_results_llm_judge.json`.

## Benchmark Results

### ESL Health QA


| Condition  | Passed    | Pass rate | Single-hop | Multi-hop |
| ---------- | --------- | --------- | ---------- | --------- |
| memory-off | 22 / 120  | 0.183     | 17 / 94    | 5 / 26    |
| memory-on  | 117 / 120 | 0.975     | 94 / 94    | 23 / 26   |


Memory improves the LLM-judged pass rate from 18.3% to 97.5%, a gain of 95 questions. The largest difference is on timeline recall: without structured health memory, the agent quickly loses access to dense day-by-day wearable and care facts. With memory enabled, stable profile data remains in `HEALTH.md`, while date-specific and event-specific timeline facts remain searchable in `health_log.jsonl`.

The memory-on run passes all 94 single-hop questions and misses only 3 multi-hop questions. The remaining failures are one profile aggregation question about height/weight and two body-mass-index change questions across dates, suggesting that direct fact recall is strong while a few cross-date numeric comparison queries still need more robust calculation support.

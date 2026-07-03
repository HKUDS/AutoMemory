---
name: auto-memory-refine
description: Run the Critic-Improver refine loop until completion
argument-hint: agent-repo-path
alwaysApply: true
---

# auto-memory-refine

**Target agent repository**: $ARGUMENTS

## Prerequisites

`/auto-memory-implement` must have completed, leaving
`worktraces/refine_state.json` with `step_status: "ready"`.

## Goal

Run the full Critic-Improver refine loop: iterate phases until the Critic
stops or `max_phases` is reached. This command owns the outer loop and
contains the complete refine protocol.

## State Discipline

- `refine_state.json` is the **sole active state** during refine. All
  phase data (scores, weakness, improvement, decision) and the
  `memory_paths` composition (set by Phase 0 Critic) live here.
- `note.md`, `memory_design.md`, and `implemented.md` together form the
  **ground truth** of refine: user requirements, intended behavior, and
  the actual built memory components. The Critic reads all three plus
  the memory source code as the authoritative reference for what the
  system is supposed to do. They are **read-only** during the loop.
- On exit (loop finished or interrupted), append a brief summary to
  `note.md` covering what happened across all phases.

## What to Do

```
loop:
  1. Read `step_status` from `worktraces/refine_state.json`.
  2. Branch on status:
     - "ready"           → run Phase 0 (baseline) per the protocol below
     - "critic_finished"  → run the next Improver + Critic phase
     - "completed"        → break
  3. After the phase, re-read `refine_state.json` and go to 1.

on exit:
  Append a summary to note.md (phases completed, final scores, outcome).
```

---

# Refine Protocol

Adversarial Critic–Improver loop: the Improver strengthens the memory
system while an independent Critic tries to break it. Maximum 7 phases.
This step assumes functional tests already pass — the goal is **quality**,
not correctness.

**Core invariant**: The Critic ALWAYS runs as a separate subagent
(Task tool). You are the Improver — you NEVER run traces or score.
This applies to EVERY phase, including Phase 1+.

**Important before start**:
- `worktraces/refine_state.json` should already exist with `step_status: "ready"`
  (created by `/auto-memory-implement`). If it does not exist, create it
  using the template at `@Auto_Memory/scaffolds/refine_state_template.json`.
- `note.md` is **read-only** during refine. The Critic reads it for user
  requirements and design context. Only append a summary at loop exit.
- Keep `runner_path` set to a reusable **project-native trace runner command**
  (do not introduce a Python wrapper for non-Python projects).

---

## Terminology

**Retention window**: the most recent K turns or tokens that directly visible 
  in the model input as context.

In refine trace replay, each trace turn represents a unit of raw history.
The runner can force a window flush through the component's `flush_window`
method (if declared). This aims to test the memory system without actually
creating retention-window-sized tokens in traces, to keep replay efficient.
Recommended: flush the window every 1–2k tokens or 10–20 turns.

**Current phase**: numeric progress marker in `refine_state.json` that tracks
which refine phase the loop is currently in. Stored as `current_phase`, with
the configured ceiling stored as `max_phases`.

**Step status**: step-end status in `refine_state.json`, stored as
`step_status`.
- `ready`: implementation is complete; the refine loop has not started yet.
- `critic_finished`: the latest completed step was a Critic step.
- `improver_finished`: the latest completed step was an Improver step.
- `completed`: the refine loop has stopped.

---

## Roles

### Improver (you — the parent agent)

Has full codebase access including memory source code, functional tests,
note.md, and `ask_ref`. CAN NOT control or modify trace generation, scoring, or
exit decisions. Your mission is to fix the memory source code according to 
the Critic's weakness diagnosis.

**Important**: If two consecutive phases show diminishing improvement and problems remain,
attempt a **structural change** (rethink extraction strategy, change storage
schema, rewrite recall ranking) rather than incremental tweaks.

### Critic (separate subagent)

Grey-box tester. Has `note.md`, `memory_design.md`, `implemented.md`,
memory source code (read-only), and prior scores. May read memory source
code to understand component behavior. MUST NOT modify any source file.
Test assertions must target observable behavior (inputs/outputs of
declared write/read methods), not internal variable names or private
helper functions. You CAN modify memory traces, create runner (in phase0),
decide path composition (in phase0).

Success is measured by finding failures — if all scores are 1.0, tests
were too easy.

The Critic MUST use a model at least as capable as the Improver's. It may
delegate trace turn content generation to a faster model, but scenario
design, scoring, and diagnosis must run on the full model.

---

## Flow

### Phase 0: Baseline

Launch the Critic with the template below. In Phase 0, the Critic:
1. Reads all component contracts from `implemented.md` and reads memory
   source code to understand component interactions.
2. Decides **path composition**: which components form each memory path
   (tested together). Records the result in
   `refine_state.json.memory_paths`. This decision is **immutable** for
   all subsequent phases.
3. Writes the initial reusable trace runner script that directly imports
   and calls component write/read methods (no adapter layer). Persists
   the runner command into `refine_state.json.runner_path`.
4. Generates the initial trace suite (per-path traces + e2e).
5. Validates `worktraces/refine_state.json` with
   `@Auto_Memory/scaffolds/check_refine_state.py`.
6. Runs all traces, scores results, diagnoses weaknesses, and only considers
   difficulty escalation if the current suite no longer exposes clear failures.
7. Updates `worktraces/refine_state.json` as the Phase 0 Critic checkpoint.
8. Runs @Auto_Memory/scaffolds/check_refine_state.py to validate the state.

### Phase 1+: Adversarial Phases

Each phase:
1. **Improver step**
   - Read the Critic's weakness diagnosis from the previous subagent report.
   - Modify the **agent's actual memory code**.
   - Ensure functional tests still pass.
   - Update `worktraces/refine_state.json` as the Improver step checkpoint.
   - Run `@Auto_Memory/scaffolds/check_refine_state.py` to validate the state.
   - Clear ALL memory data consolidated in the previous phase so Critic can test 
      from a clean state.
2. **Critic step**
   - Launch a NEW Critic subagent with the template plus Phase 1 addendum.
   - Before running traces, validate `worktraces/refine_state.json` with
     `@Auto_Memory/scaffolds/check_refine_state.py`. If validation fails, stop
     and report.
   - Run traces for all **non-graduated** paths plus `e2e`
     (skip traces belonging to graduated paths; carry forward their
     most recent scores).
   - Score results and diagnose weaknesses per path independently.
   - **Per-path escalation**: for each non-graduated path with no
     weakness, escalate it (add one trace or strengthen one), then
     rerun that path + `e2e`. Other paths' weaknesses do NOT block
     this. At most 2 escalation slots per phase for each path.
     A path surviving two escalations at ≥ 0.9 graduates (appended
     to `graduated_paths`).
   - Update `worktraces/refine_state.json` as the Critic step checkpoint.
   - Run `@Auto_Memory/scaffolds/check_refine_state.py` again after updating
     state.

### Exit Conditions

Exit is **Critic-controlled**.
1. **All paths graduated**: every non-graduated path graduates in this
   phase (each path survives two escalations at ≥ 0.9), **and** the
   `e2e` trace's metrics stay above 0.9.
2. **Max phases reached**: 7 phases completed.

---

## Required Step-End State Updates

State is updated directly in `worktraces/refine_state.json`, but each step
must end with a globally consistent checkpoint.

- Top-level refine config that persists across steps:
  - `current_phase`
  - `max_phases`
  - `step_status`
  - `runner_path`
  - `memory_paths` (set by Phase 0 Critic; immutable after Phase 0)
  - `graduated_paths` (paths that survived two escalations at ≥0.9; append-only)

- After every **Critic step**, update:
  - `current_phase`
  - `step_status = critic_finished`
  - `trace_registry` (if traces were added/modified)
  - current phase entry in `phase_history` for `phase = state.current_phase`:
    - `scores`
    - `new_traces`
    - `weakness`
    - `decision`
  - top-level `stop_decision`

- After every **Improver step**, update:
  - `current_phase`
  - `step_status = improver_finished`
  - current phase entry in `phase_history` for `phase = state.current_phase`:
    - `improvement`
  - any handoff state needed for the next Critic step

---

## Launching the Critic

The Critic prompt is generated by a script — the Improver does NOT
write or modify the prompt text.

To launch the Critic:
1. Run the prompt generator (substitute actual absolute paths and phase number):
   ```
   python @Auto_Memory/scaffolds/fill_critic_prompt.py \
       --note-md-path {note_md_path} \
       --memory-design-md-path {memory_design_md_path} \
       --implemented-md-path {implemented_md_path} \
       --refine-state-path {refine_state_json_path} \
       --phase {current_phase}
   ```
2. Capture the stdout output — this is the complete Critic prompt.
3. Pass the captured output **verbatim** as the `prompt` parameter to the
   Task tool. Do NOT append, prepend, or modify the text in any way.

The script handles variable substitution and Phase 0/Phase 1+ addenda
automatically. The Improver's only job is to supply the correct paths
and phase number.

---

## Rules

1. **Critic tests goals, not implementation.**
2. **LLM honest** Traces should be ran with actual llm if it is involved in memory, not a mock llm.
3. **No trace deletion.** Existing traces can be modified but not removed.
4. **Scores are Critic-owned.** Improver does not run traces or score.
5. **No weakening tests to raise scores.**
6. **State is JSON-owned.** `worktraces/refine_state.json` is authoritative.
7. **Critic controls stop.** Only Critic may set `stop_decision.decision=stop`.
8. **Single reusable runner command.** Reuse `runner_path`; do not create per-phase runner forks.
9. **Path composition is immutable.** `memory_paths` in `refine_state.json`
   is set by the Phase 0 Critic and MUST NOT be modified in Phase 1+.
10. **Escalation is per-path.** A path with no weakness gets escalated;
   other paths' weaknesses are irrelevant. See protocol § Per-Path Lifecycle.

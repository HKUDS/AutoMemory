# Refine Protocol

Reference document for the Critic agent. Read this file in full before
any other action.

## Terminology

**Retention window**: the most recent K turns or tokens directly visible
in the model input as context.

In refine trace replay, each trace turn represents a unit of raw history.
The runner can force a window flush through the component's
`flush_window_method` (if declared). This aims to test the memory system
without creating retention-window-sized tokens in traces.
Recommended: flush every 1–2k tokens or 10–20 turns.

**Memory component**: an individual memory store or mechanism declared in
`implemented.md` (section `Memory Components`). Each component has:
- `write_method`, `write_trigger`, `read_method`, `read_returns`
- `eval_mode` (`qa` or `hit_rate`)
- `persistence`, `requires_flush_window`, `flush_window_method` (when
  `requires_flush_window` is true), `dependencies`, `notes`

Components with `write_trigger: none` are **fixture-only**: seed them
for e2e context but do not test their write path.

**current_phase**: numeric progress marker in `refine_state.json`.
Ceiling is `max_phases`.

**step_status** in `refine_state.json`:
- `ready` / `critic_finished` / `improver_finished` / `completed`

---

## Memory Path

A memory path is the full production information flow from a raw input boundary
to a durable/retrievable memory endpoint. Components/tools/stores/extractors are
only mechanisms inside that path.

Every refine trace must start from the raw production input for its path and run
through the production stages that make that input into memory, including LLM
stages and consolidation/Dream/batch stages when they are part of that path.
The trace must verify the resulting memory is durable or retrievable.

Do not create component-level traces; implementation/unit tests already cover
component contracts. Direct tool/store/extractor calls or structured
intermediate records are invalid unless they are truly the raw production input
of that path. If production uses an LLM for tool selection, extraction,
summarization, consolidation, deduplication, reconciliation, or salience
judgment, the trace must call the real LLM stage and must not feed the LLM's
expected output directly into downstream components.

Path Examples:
1. On-policy memory captured by agent tool call:
   - Good: raw user message → real agent/model loop → model decides whether and
     how to call memory tool → production tool execution → durable/retrievable
     memory assertion.
   - Bad: directly feed formatted information into tools.
2. Off-policy memory captured by retention-window consolidation:
   - Good: raw retention-window history/turns → production consolidation rules
     and LLM stages if any → durable/retrievable memory assertion.
   - Bad: directly provide consolidated facts or `flush_entries`, and only test
     whether they can be stored.

---

## Trace Suite Design

The trace suite is organized **per memory path** plus one cross-path
end-to-end (`e2e`) trace. The path list comes from `memory_paths` in
`refine_state.json`.

### Suite shape

- 1–2 traces per memory path.
- Exactly 1 `e2e` trace exercising multiple paths together.
- Total trace count >= 3.
- Single-path systems: that path **is** the e2e path; aim for 3 traces;
  no separate `e2e` trace required.

### Evaluation modes

1. Method
- **qa** (default): scored by `support_coverage` and `query_success`.
  Traces have `recall_queries` with `expected_information` items.
- **hit_rate**: for cache-like components. Write-then-read verification.
  Scored as `hit_rate = hits / total_checks`.

Traces declare `eval_mode` at the top level (defaults to `qa`).

2. On-policy vs off-policy

The component's `write_trigger` determines synthesis mode:
- **Off-policy** (`system_trigger`): trace contains simulated turns;
  runner calls write methods directly.
- **On-policy** (`agent_tool_call`): trace contains user turns only;
  the real agent decides what to memorize.

The `e2e` trace mirrors the production path mix.

### Trace naming

`trace_01_<path>_<issue>` or `trace_02_e2e_<issue>`, replace `id` with actual number

### Testing direction suggestions

When designing traces, the suite should collectively cover the following
dimensions where applicable to the component's declared contract:

**Write semantics**
- **Append**: new information added alongside existing entries.
- **Update**: newer information overwrites or merges with a prior entry
  on the same key/topic.
- **Delete / discontinue**: information explicitly removed, marked
  resolved, or invalidated.
- **No-write**: turns that carry no memorizable content; the component
  should not create spurious entries.
- **Deduplication**: repeated or near-identical information should not
  produce duplicate entries.

**Read-side stress**
- **Write–read context shift**: the query context differs from the
  original write context (different phrasing, later time, changed
  surrounding state). Tests whether retrieval is robust to context
  drift.
- **Ambiguous / under-specified query**: the query is vague or matches
  multiple stored items. Tests whether the component returns the most
  relevant results rather than nothing or everything.

Not every component needs every dimension. Match the dimensions to the
component's contract — e.g. an append-only log has no update/delete
path; a cache has no ambiguous-query concern. The goal is to ensure the
suite does not accidentally test only the happy-path append-then-recall
scenario.

---

## Trace Replay

Keep in mind:
1. Each trace runs in memory isolation. 
2. Parallel execution is allowed with isolated data directories. 
3. Ensure the initial storage is empty before each trace runs. Previous
  run's content should not affect the current trace.

### qa mode

```
function replay_qa_trace(trace, component_init_fn, data_dir):
    components = component_init_fn(data_dir / trace.trace_id)

    for turn in trace.turns:
        for component in path.components:
            component.write(turn_context)

    if path.requires_flush_window:
        for component in path.components:
            if component.flush_window_method:
                component.flush_window_method()

    results = []
    for q in trace.recall_queries:
        outputs = {}
        for component in path.components:
            outputs[component.name] = component.read(q.query)
        results.append({query: q.query, outputs: outputs})
    return results
```

### hit_rate mode

```
function replay_hit_rate_trace(trace, component_init_fn, data_dir):
    components = component_init_fn(data_dir / trace.trace_id)

    hits = 0; total = 0
    for check in trace.checks:
        component.write(check.write_input)
        result = component.read(check.read_input)
        total += 1
        if matches(result, check.expected_output):
            hits += 1
    return {hit_rate: hits / total}
```

- All ingestion goes through declared write methods; do NOT bypass.
- For multiple LLM calls, consider parallel execution with isolation.

---

## Runner Rules

- Reuse the existing `runner_path` unless missing/invalid.
- No per-phase runner forks.
- Phase 0: create the runner once and persist.
- Runner must respect `write_trigger`, `requires_flush_window`, and
  `flush_window_method` from `implemented.md`.
- Save trace files in `traces/` next to `refine_state.json`.

---

## Scoring Metrics

### qa mode

- `support_coverage`: fraction of `expected_information` items matched.
- `query_success`: 1 only if all items match, 0 otherwise.
- Items are binary-scored (1 if matcher passes, 0 otherwise).
- Matchers: substring, regex, or semantic. Thresholds convert to binary.
- Keep `expected_information` atomic and concrete.

### hit_rate mode

- `hit_rate`: fraction of write-then-read checks that returned expected
  content. Each check is binary.

### Reporting

Scores reported **per trace**, aggregated **per path** plus separate
`e2e` aggregate. Each trace reports metrics for its `eval_mode`.
For graduated paths whose traces were not run this phase, carry forward
their most recent scores into the current `phase_history.scores`.

---

## Trace Quality Gates

Every path trace MUST pass:
- Minimum queries >= 5 (qa) or checks >= 5 (hit_rate).
- Turns covered by queries <= 30% of total turns.
- Realistic noise.
- Size gate by consolidation type:
  - per-window: `flush_window` calls >= 2 (~30-40 turns or 6k tokens)
  - per-turn: 10-20 turns
  - per-trajectory: 3-10 trajectories

For e2e: check all components collaborate without functional redundancy.

---

## Per-Path Lifecycle

Each memory path progresses independently through three stages.
Decisions for one path are **never blocked** by another path's
weaknesses or scores.

### Active (default)

Every Critic step runs traces for all **non-graduated** paths plus the
`e2e` trace. Traces belonging to graduated paths are **skipped** — their
most recent scores carry forward.

After scoring and triage for a given active path:
- **Has weakness** (memory-code issue identified): report it in
  `phase_history.weakness`. The Improver addresses it next phase.
  Do NOT escalate this path.
- **No weakness** (no memory-code issue found; any test-side problems
  already fixed by Critic): this path enters escalation.

### Escalation

The Critic raises difficulty for the path: add one new trace or
strengthen an existing trace, then rerun only that path's traces + the
`e2e` trace.

- Triggered **per-path** when that path has no weakness after the
  initial run of the phase. Other paths' weaknesses are irrelevant.
- At most 2 escalation slots per phase for each path.
- Paths in `graduated_paths` are excluded from escalation.
- After escalation rerun, re-evaluate: if the path now shows weakness,
  report it normally. If still no weakness, the escalation counts as
  survived.

### Graduation

A path that survives two escalations within a single phase with all
metrics ≥ 0.9 is appended to `graduated_paths` in `refine_state.json`.

- `graduated_paths` is append-only — once graduated, never removed.
- **Graduated paths' traces are NOT run.** Their most recent scores
  carry forward into `phase_history.scores` for reporting and exit-
  condition evaluation.
- Excluded from further escalation.

---

## Exit Conditions

Exit is **Critic-controlled**.
1. **All paths graduated**: every non-graduated path graduates in this
   phase (survives two escalations at ≥ 0.9), AND the `e2e` trace's
   metrics stay above 0.9.
2. `max_phases` reached.

---

## Required Step-End State Updates

After every Critic step, update `refine_state.json`:
- `current_phase`, `step_status = critic_finished`
- `trace_registry` (if changed)
- Current phase entry in `phase_history`:
  `scores`, `new_traces`, `weakness`, `decision`
- `stop_decision`

Top-level config persists across steps:
`current_phase`, `max_phases`, `step_status`, `runner_path`,
`memory_paths`, `graduated_paths`.

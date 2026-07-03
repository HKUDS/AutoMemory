# Trace Format

Synthetic traces are JSON files synthesized and replayed during **refine**.
Each trace simulates one or more agent interactions.

## Schema

### qa mode (default)

```json
{
  "trace_id": "string — unique identifier (e.g. trace_id_mypath_overflow)",
  "eval_mode": "qa",
  "category": "normal | edge_case | multi_session | adversarial | high_noise",
  "description": "One-sentence summary of what this trace exercises",
  "turns": [
    {
      "role": "user | agent | tool | system",
      "content": "string — the raw message or output",
      "used_for_evaluation": "boolean — whether this turn should count as evaluation-bearing context when the Critic designs questions and judges recall",
      "metadata": {
        "tool_name": "optional — name of the tool called",
        "tool_input": "optional — what was passed to the tool",
        "tool_output": "optional — what the tool returned",
        "session_id": "optional — for multi-session traces",
        "retention_flush": "optional bool — mark a turn that causes retention-window eviction"
      }
    }
  ],
  "recall_queries": [
    {
      "query": "string — the follow-up question or recall prompt",
      "expected_information": [
        {
          "value": "string — atomic fact or information item required to answer the query correctly",
          "matcher": "substring | regex | semantic",
          "threshold": "optional float in [0,1]; count as matched iff matcher score >= threshold"
        }
      ]
    }
  ]
}
```

### hit_rate mode

For cache-like components where the goal is to verify write-then-read
correctness rather than recall quality.

```json
{
  "trace_id": "string — unique identifier (e.g. trace_id_cache_basic)",
  "eval_mode": "hit_rate",
  "category": "normal | edge_case | adversarial",
  "description": "One-sentence summary of what this trace exercises",
  "checks": [
    {
      "description": "string — what this check verifies",
      "write_input": "object — arguments passed to the component's write method",
      "read_input": "object — arguments passed to the component's read method",
      "expected_output": {
        "value": "string — expected content in the read result",
        "matcher": "substring | regex | exact",
        "description": "string — what should match"
      }
    }
  ]
}
```

## Conventions

### General

- `eval_mode` defaults to `"qa"` if omitted.
- Traces with `eval_mode: "qa"` use `turns` + `recall_queries`.
- Traces with `eval_mode: "hit_rate"` use `checks`.
- A trace MUST NOT mix both formats.

### qa mode conventions

- `turns` is ordered chronologically.
- For multi-session traces, include a system turn with
  `"content": "--- session boundary ---"` between sessions.
- `recall_queries` lists follow-up questions where the memory system should
  provide enough support to answer correctly.
- `used_for_evaluation=true` means the turn is fair game for the Critic when
  deciding what the memory system should have retained or answered from.
  It does **not** mean "this turn should be extracted into memory verbatim."
  A tool call, a short user instruction, or a structured result may all be
  evaluation-bearing even if they are not directly memorizable.
- `expected_information` should be a flat list of the information items needed
  to answer the query correctly.
- Each `expected_information` item is scored as a binary match:
  - `1` if the configured matcher passes
  - `0` otherwise
- String items default to `substring` matching.
- Object items may override the matcher with:
  - `substring`: direct lexical containment
  - `regex`: regex match against memory/recall output
  - `semantic`: semantic similarity or LLM-based judge
- `threshold` is only used to convert matcher output into a boolean match. If
  the matcher score is greater than or equal to the threshold, the item counts
  as matched; otherwise it counts as unmatched.
- `support_coverage` is computed per query as the fraction of
  `expected_information` items that match.
- `query_success` is computed per query as a binary result: success only if all
  items in `expected_information` match.
- Recall queries should use the vocabulary a real user would naturally use,
  which may differ from how the information was originally stated or stored.

### hit_rate mode conventions

- Each `check` is a write-then-read verification: write known content via
  the component's write method, then read and verify the content is returned.
- Each check is scored as binary: `1` (hit) if the matcher passes, `0` (miss).
- `hit_rate` is computed as `hits / total_checks` for the trace.
- Matchers for hit_rate checks:
  - `substring`: read output contains the expected value (case-insensitive)
  - `regex`: read output matches the regex pattern
  - `exact`: read output equals the expected value exactly
- Checks should cover: basic write-then-read, overwrite/update, cache
  invalidation or expiry, and key-miss (read for content never written
  should return empty/null).

## Trace Design Guidance

- Mark evaluation-bearing turns explicitly with `used_for_evaluation`.
- Use `metadata.retention_flush=true` when you want to show a turn that causes
  retention-window pressure or eviction.
- For multi-session traces, include a clear session boundary turn.
- Keep `expected_information` atomic and concrete. Split compound requirements
  into separate items instead of encoding weights or "critical vs optional"
  distinctions.
- Prefer simple string items unless you specifically need regex or semantic
  matching for a query.
- Include at least some queries that are not trivial surface-form restatements
  of the original evidence, especially for agents expected to support indirect
  retrieval.

## File Naming

Place traces in `worktraces/traces/`:
- `trace_id_<path>_<issue>.json` — per-path traces
- `trace_id_e2e_<issue>.json` — end-to-end traces

All trace ids start with `trace_id_`, followed by the path name (or
`e2e`), then the targeted issue.

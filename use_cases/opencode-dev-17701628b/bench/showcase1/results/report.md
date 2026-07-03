# showcase1 Results

Per-session rows use **`tokensSent` + `tokensReceived`** (= stored `inputTokens` + `outputTokens`) as Total Tokens; **`stepsToFirstTouch`** counts assistant rounds until first **read/grep** touching an expected file from `session-meta.json`.

| Session | Task | Condition | Resolved | src | DryRun | Tokens | Cost | core b/g/r/e/w | touch | steps | xvs | flake8 |
|---:|---|---|---|---|---:|---:|---|:---:|---:|---:|---|---|
| 1 | pytest-dev__pytest-5262 | memory-off | true | swebench | false | 271999 | 0.03414312 | 7/3/5/1/0 | 2 | 18 | false | false |
| 2 | pytest-dev__pytest-7432 | memory-off | true | swebench | false | 110633 | 0.023212860000000002 | 4/0/1/1/0 | 1 | 6 | false | false |
| 3 | pytest-dev__pytest-8399 | memory-off | true | swebench | false | 496205 | 0.04480542 | 26/0/1/1/0 | 2 | 28 | false | false |
| 4 | pytest-dev__pytest-10081 | memory-off | true | swebench | false | 429958 | 0.05158494000000001 | 15/0/6/1/0 | 2 | 23 | false | false |
| 5 | pytest-dev__pytest-7571 | memory-off | true | swebench | false | 576588 | 0.05255970000000001 | 15/1/2/3/0 | 1 | 21 | false | false |
| 6 | pytest-dev__pytest-10051 | memory-off | false | swebench | false | 201645 | 0.0175383 | 7/1/3/1/1 | 1 | 15 | true | false |
| 1 | pytest-dev__pytest-5262 | memory-on | true | swebench | false | 157090 | 0.043990919999999996 | 7/1/1/1/0 | 1 | 11 | false | false |
| 2 | pytest-dev__pytest-7432 | memory-on | true | swebench | false | 91528 | 0.027982319999999998 | 3/0/1/1/0 | 1 | 6 | false | false |
| 3 | pytest-dev__pytest-8399 | memory-on | true | swebench | false | 115478 | 0.03162558 | 2/2/1/1/0 | 4 | 8 | false | false |
| 4 | pytest-dev__pytest-10081 | memory-on | true | swebench | false | 147131 | 0.04041197999999999 | 3/2/3/1/1 | 1 | 10 | false | false |
| 5 | pytest-dev__pytest-7571 | memory-on | true | swebench | false | 236121 | 0.06650123999999999 | 5/3/3/3/0 | 1 | 15 | false | false |
| 6 | pytest-dev__pytest-10051 | memory-on | true | swebench | false | 720951 | 0.21437231999999998 | 14/8/11/3/1 | 1 | 38 | true | false |

## Plan metrics summary

### Resolve rate (SWE-bench docker, from `resolved` / `swebench-verify` JSON)

| Condition | Resolved | Known | Rate |
|---|---:|---:|---:|
| memory-off | 5 | 6 | 0.833 |
| memory-on | 6 | 6 | 1.000 |

### Total tokens & tool calls (all sessions in showcase)

| Condition | Σ tokens | Σ tool calls |
|---|---:|---:|
| memory-off | 2087028 | 110 |
| memory-on | 1468299 | 85 |

### Forward transfer per task: FT = (resolve_memory_on ? 1 : 0) − (resolve_memory_off ? 1 : 0)

| Session | instance_id | mem-on | mem-off | FT |
|---:|---|---|:---:|---:|
| 1 | pytest-dev__pytest-5262 | true | true | 0 |
| 2 | pytest-dev__pytest-7432 | true | true | 0 |
| 3 | pytest-dev__pytest-8399 | true | true | 0 |
| 4 | pytest-dev__pytest-10081 | true | true | 0 |
| 5 | pytest-dev__pytest-7571 | true | true | 0 |
| 6 | pytest-dev__pytest-10051 | true | false | 1 |

**Mean FT** (tasks with both resolves known): 0.167

## Notes

- **Resolve rate**: `resolved` is read from `session-*/resolved.json` if present, else from `bench/swebench-verify/opencode-memory-eval-<condition>.opencode-memory-eval-<condition>.json` (`resolved_ids` / `unresolved_ids`).
- **Forward transfer**: per-task difference of resolve flags; mean taken over tasks where both conditions have a known outcome.
- **Memory artifacts**: `memoryToolStatsLines` / `memoryFactsLines` count lines in shared `.opencode/memory/` when those files exist (tool experience / facts signal).
- Showcase 2 additionally uses preference booleans (`pytest -xvs`, `flake8`) in `preference`.
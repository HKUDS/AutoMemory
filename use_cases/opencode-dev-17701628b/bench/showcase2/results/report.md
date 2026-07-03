# showcase2 Results

Per-session rows use `**tokensSent` + `tokensReceived**` (= stored `inputTokens` + `outputTokens`) as Total Tokens; `**stepsToFirstTouch**` counts assistant rounds until first **read/grep** touching an expected file from `session-meta.json`.


| Session | Task                 | Condition  | Resolved | src      | DryRun | Tokens  | Cost                | core b/g/r/e/w | touch | steps | xvs   | flake8 |
| ------- | -------------------- | ---------- | -------- | -------- | ------ | ------- | ------------------- | -------------- | ----- | ----- | ----- | ------ |
| 1       | django__django-13964 | memory-off | false    | swebench | false  | 1303916 | 0.14173248000000005 | 15/10/14/0/0   | 2     | 38    | false | false  |
| 2       | django__django-13964 | memory-off | false    | swebench | false  | 4174022 | 0.3743940599999999  | 58/15/14/2/0   | 3     | 73    | false | false  |
| 3       | django__django-13933 | memory-off | true     | swebench | false  | 451857  | 0.04940994          | 8/2/9/6/0      | 1     | 27    | false | false  |
| 1       | django__django-13964 | memory-on  | false    | swebench | false  | 53500   | 0.01586166          | 0/0/2/0/0      | 2     | 4     | false | false  |
| 2       | django__django-13964 | memory-on  | false    | swebench | false  | 305743  | 0.08991444          | 14/2/2/1/0     | 1     | 19    | false | true   |
| 3       | django__django-13933 | memory-on  | true     | swebench | false  | 263988  | 0.07585373999999999 | 5/2/6/2/0      | 1     | 16    | false | false  |


## Plan metrics summary

### Resolve rate (SWE-bench docker, from `resolved` / `swebench-verify` JSON)


| Condition  | Resolved | Known | Rate  |
| ---------- | -------- | ----- | ----- |
| memory-off | 1        | 3     | 0.333 |
| memory-on  | 1        | 3     | 0.333 |


### Total tokens & tool calls (all sessions in showcase)


| Condition  | Σ tokens | Σ tool calls |
| ---------- | -------- | ------------ |
| memory-off | 5929795  | 158          |
| memory-on  | 623231   | 38           |


### Forward transfer per task: FT = (resolve_memory_on ? 1 : 0) − (resolve_memory_off ? 1 : 0)


| Session | instance_id          | mem-on | mem-off | FT  |
| ------- | -------------------- | ------ | ------- | --- |
| 1       | django__django-13964 | false  | false   | 0   |
| 2       | django__django-13964 | false  | false   | 0   |
| 3       | django__django-13933 | true   | true    | 0   |


**Mean FT** (tasks with both resolves known): 0.000

## Notes

- **Resolve rate**: `resolved` is read from `session-*/resolved.json` if present, else from `bench/swebench-verify/opencode-memory-eval-<condition>.opencode-memory-eval-<condition>.json` (`resolved_ids` / `unresolved_ids`).
- **Forward transfer**: per-task difference of resolve flags; mean taken over tasks where both conditions have a known outcome.
- **Memory artifacts**: `memoryToolStatsLines` / `memoryFactsLines` count lines in shared `.opencode/memory/` when those files exist (tool experience / facts signal).
- Showcase 2 additionally uses preference booleans (`pytest -xvs`, `flake8`) in `preference`.


# OpenCode Memory Evaluation Showcase Summary

This summary is generated from the dry-run scaffold currently present under `opencode/bench/`. Full model execution can be started with `bun bench/harness.ts --execute` from the repository root.

## showcase1

| Condition | Sessions | Dry Run Sessions | Tool Calls | Tokens Available |
|---|---:|---:|---:|---:|
| memory-off | 4 | 4 | 0 | 0 |
| memory-on | 4 | 4 | 0 | 0 |

- Detailed report: `showcase1/results/report.md`
- Metrics JSON: `showcase1/results/metrics.json`

## showcase2

| Condition | Sessions | Dry Run Sessions | Tool Calls | Tokens Available |
|---|---:|---:|---:|---:|
| memory-off | 3 | 3 | 0 | 0 |
| memory-on | 3 | 3 | 0 | 0 |

- Detailed report: `showcase2/results/report.md`
- Metrics JSON: `showcase2/results/metrics.json`

## Execution Notes

- The checked-in run artifacts are dry-run artifacts: prompts, configs, session metadata, placeholder logs, and empty patches were generated without invoking a model.
- `swebench` was installed and metadata fetching is implemented via `scripts/fetch_instances.py` with a direct HuggingFace parquet fallback.
- Real patches, memory JSONL files, token telemetry, and SWE-bench pass/fail statuses are populated after running with `--execute` and optionally `--verify`.

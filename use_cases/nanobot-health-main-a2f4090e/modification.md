# Modifications

## Benchmark Time Override

Nanobot now supports overriding the runtime "current time" through the
`NANOBOT_CURRENT_TIME` environment variable. This allows benchmark replays to
run with the historical date from the dataset instead of the machine's actual
current date.

The override is used by the shared time helpers, so it affects:

- Runtime context shown to the model as `Current Time`
- Session message timestamps
- Memory/history timestamps generated during a run

## ESL Benchmark Scripts

`bench/run_agent.py` now passes a synthetic current time for each replayed user
turn. Timeline turns use their own `date` field at noon, for example
`2026-01-13T12:00:00`. Profile turns without a date inherit the most recent
timeline date, preserving the chronological context during ingestion.

`bench/eval_agent.py` supports `--current-time` for explicit QA-time override.
When this option is not provided, it defaults to the latest dated turn in
`user_turn_mixed.json`, also at noon. This prevents evaluation questions about
January or February benchmark data from being answered under the real wall-clock
date.


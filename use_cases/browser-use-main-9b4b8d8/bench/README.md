# Supplemental Memory Showcases

This directory contains runnable benchmarks for demonstrating supplemental memory.

## Showcases

`github` demonstrates repeated information seeking on a familiar site:

```bash
uv run python bench/supplemental_memory_showcases.py github --mode all
```

It runs grouped tasks against `https://github.com/browser-use/browser-use`, including repo metadata, Issues, PRs, Actions, README, and examples/docs navigation. GitHub counts are dynamic, so memory should only help with navigation and extraction context. Runs must still verify live values.

`failure` demonstrates failure reuse on a deterministic local dashboard:

```bash
uv run python bench/supplemental_memory_showcases.py failure --mode all --max-steps 20
```

The local page includes a blocking cookie modal, a fake Search button, a real alternate entry path, and a details tab that requires waiting or scrolling. The memory-on run should retrieve prior blockers and successful workarounds learned earlier in the same sequence.

## Modes

- `memory-on`: tasks share an initially empty `BrowserUseMemory` store and accumulate memory as the sequence runs.
- `memory-off`: every task uses an independent agent without supplemental memory.
- `all`: runs the comparison modes together in order: **`memory-on` → `memory-off`**.
- `--max-round N`: repeats the selected task sequence N times per mode. `memory-on` keeps the same memory store across rounds; `memory-off` stays memory-free in every round.

Useful options:

```bash
uv run python bench/supplemental_memory_showcases.py all \
	--provider openrouter \
	--model openai/gpt-4o-mini \
	--limit-tasks 2 \
	--max-round 3 \
	--headed
```

`--fresh` deletes everything under `--results-dir` (default `bench/results`) before starting, so old run folders and traces are removed; the new run still writes to a new `bench/results/<timestamp>/` directory.

Provider defaults to OpenRouter and reads `.env` via `python-dotenv`. Supported providers are `openrouter`, `openai`, and `google`.

## Outputs

Each run writes to `bench/results/<timestamp>/`:

- `summary.csv`: metrics table.
- `summary.md`: compact Markdown comparison.
- `runs/*/history.json`: full agent history.
- `runs/*/memory_injection_before.txt`: supplemental memory context available before the run.
- `runs/*/memory_injection_after.txt`: supplemental memory context available after the run commits memory.
- `runs/*/memory_injection.txt`: compatibility file containing before and after contexts.
- `runs/*/result.json`: per-run metric row.

The shared metrics are:

| Case | Mode | Round | Steps | Total tokens | Input tokens | Clicks | Extract/search calls | Duration | Correct |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|

The evidence column reports retrieved block tags such as `<retrieved_run_cache>`, `<cached_page_observations>`, `<start_sufficiency_check>`, and `<site_memory>`.

## Demo Claims

For GitHub:

> Memory helps repeated information-seeking on a familiar site by reusing navigation/extraction context.

For failure reuse:

> Memory helps avoid repeated failures by preserving prior blockers and successful workarounds.

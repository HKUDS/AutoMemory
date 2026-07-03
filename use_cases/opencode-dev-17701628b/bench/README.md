# OpenCode Memory Evaluation Showcase

This directory contains a reproducible scaffold for evaluating OpenCode long-term memory on coding-agent tasks.

The benchmark compares two conditions:

- `memory-on`: OpenCode memory is enabled and sessions share `.opencode/memory/`.
- `memory-off`: OpenCode memory is disabled through `opencode.jsonc`.

By default, **natural memory** behavior is unchanged: the harness does not trigger extra consolidation after each session; OpenCode writes memory on its own schedule.

**Controlled memory** (opt-in): pass `--memory-finalize` so that after each successful `memory-on` session the harness calls OpenCode’s native APIs only—`session.summarize` (compaction), `session.update` with `time.archived`, or both—via the same in-process server path the app uses. The harness never writes `facts.jsonl`, `observations.jsonl`, or `sessions.jsonl` itself; it only schedules when consolidation runs. `--memory-finalize` is ignored for `memory-off` and for dry runs (no `--execute`). Failures during finalization fail the bench run, since missing consolidation would invalidate the controlled condition.

| `--memory-finalize` | Effect |
| --- | --- |
| `none` (default) | No extra finalization. |
| `summarize` | On-demand session compaction after each session. |
| `archive` | Mark session archived (trajectory persistence path). |
| `summarize-archive` | Summarize, then archive. |

Each finalized session records metadata in `session-*/memory-finalize.json` (mode, `sessionID`, status, timestamp).

All code, prompts, workspaces, patches, session logs, memory stores, and reports live under `opencode/bench/`.

## Showcases

### Showcase 1: Sequential pytest issues

Source: SWE-Bench-CL / SWE-bench Verified, pytest sequence.

Two **local dependency clusters** (entry session plus follow-up in the same submodule):

- **unittest integration**: `pytest-dev__pytest-8399` → `pytest-dev__pytest-10081` (both touch `src/_pytest/unittest.py`; 8399 also touches `src/_pytest/python.py`).
- **logging / caplog**: `pytest-dev__pytest-7571` → `pytest-dev__pytest-10051` (`src/_pytest/logging.py`).

Ordered sessions:

1. `pytest-dev__pytest-5262` — capture warm-up (`capture.py`)
2. `pytest-dev__pytest-7432` — skip/reporting (`skipping.py`)
3. `pytest-dev__pytest-8399` — unittest integration entry
4. `pytest-dev__pytest-10081` — unittest integration transfer
5. `pytest-dev__pytest-7571` — logging/caplog entry
6. `pytest-dev__pytest-10051` — logging/caplog transfer

This showcase emphasizes project-context accumulation and forward transfer across sequential issues in the same repository.

### Showcase 2: Multi-session django workflow

Source: SWE-bench Verified, customized into a three-session workflow.

Tasks:

- `django__django-13964`: primary task across sessions 1 and 2.
- `django__django-13933`: related task in session 3.

This showcase emphasizes handoff continuity and user preference adherence. All prompts sent to OpenCode are English.

## Setup

The scaffold expects Bun, Python, Git, Docker, and the Python `swebench` package when running full verification.

```bash
cd /data2/junzhou2/opencode
python3 -m pip install swebench
```

The SWE-Bench-CL dataset copy is stored at:

```text
opencode/bench/data/SWE-Bench-CL-Curriculum.json
```

## Dry Run

Dry run prepares prompts, metadata, logs, empty patches, and reports without invoking OpenCode or Docker:

```bash
cd /data2/junzhou2/opencode
bun bench/harness.ts --showcase all --condition all
```

## Full Run

A full run invokes OpenCode for every session. It requires model credentials to be configured for OpenCode.

Before OpenCode starts, the harness prepares an isolated Python environment in each session repo at
`session-*/repo/.bench-venv` and prepends it to the OpenCode process `PATH`. Tool `bash` sessions may still
load conda from your `~/.bashrc` and shadow that order, so the prompt also tells the model to use
`./.bench-venv/bin/python` explicitly for tests.
Set `OPENCODE_BENCH_PYTHON` to choose the Python executable used to create these venvs; otherwise the
harness tries `python3.10`, then `python3`, then `python`.

The agent prompt also includes environment guardrails:

```text
Do not install system packages.
Do not modify Python/conda/venv environment.
Do not run pip install unless explicitly required by the task.
The repository environment is already prepared.
When running Python or tests from a shell, use the prepared interpreter only: from the repo root run `./.bench-venv/bin/python` or `./.bench-venv/bin/python -m pytest` (never rely on bare `python`, `python3`, or `pytest` on PATH — conda or system Python may take precedence).
Focus only on fixing the issue.
```

```bash
cd /data2/junzhou2/opencode
bun bench/harness.ts --showcase all --condition all --execute --model anthropic/claude-sonnet-4-20250514
```

Controlled-memory example (native summarize then archive after each `memory-on` session):

```bash
bun bench/harness.ts --showcase showcase1 --condition all --execute --memory-finalize summarize-archive --model <provider/model>
```

After a real execute run with shared memory, inspect `showcase*/runs/memory-on/shared-memory/` for native artifacts such as `observations.jsonl`, `handoff.json`, or `sessions.jsonl`, depending on compaction and archival results.

To also run SWE-bench Docker verification:

```bash
bun bench/harness.ts --showcase all --condition all --execute --verify --model anthropic/claude-sonnet-4-20250514
```

Agent-side testing and SWE-bench scoring are separate environments: the agent works in the prepared per-session
venv above, while `--verify` still runs `swebench.harness.run_evaluation` in SWE-bench Docker images.

## Outputs

Important outputs are:

- `showcase*/runs/<condition>/session-*/prompt.txt`
- `showcase*/runs/<condition>/session-*/session-log.jsonl`
- `showcase*/runs/<condition>/session-*/memory-finalize.json` (when `--memory-finalize` is not `none` for `memory-on` execute runs)
- `showcase*/runs/<condition>/session-*/patch.diff`
- `showcase*/runs/<condition>/shared-memory/`
- `showcase*/results/metrics.json`
- `showcase*/results/report.md`
- `swebench-verify/predictions.jsonl`

## Metrics

`metrics.ts` extracts:

- resolve status, when verifier output is available
- token telemetry, when OpenCode emits usage metadata
- tool call counts
- steps to first touch of expected files
- preference adherence for `pytest -xvs` and `flake8`

Reports can be regenerated with (omit `--sessions` to use `sessions.length` from each showcase’s `tasks.json`):

```bash
bun bench/metrics.ts --showcase showcase1
bun bench/metrics.ts --showcase showcase2
```

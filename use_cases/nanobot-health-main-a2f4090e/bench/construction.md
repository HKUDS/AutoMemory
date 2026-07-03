# Data processing record — `user110_AT_demo` timeline

From structured **profile** and **timeline** to evaluation **questions** and **user turns**:

```
  profile.json
       │
       ▼
  user_turn_profile.json ────────────────┐
                                         │
  timeline.json                          │
       │                                 │
       ▼                                 ├──► user_turn_mixed.json
  filtered → sparse timeline             │         (mixed ingest turns)
       │                                 │
       ├──► user_turn_timeline.json ─────┘
       │
       └──► question_sparse40.json
                 (120 QA items)
```

## Directory layout (current)


| Path                                          | Role                                                                                                                                                                                        |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `data/construction(2026-04)/user110_AT_demo/` | **Construction:** raw slice, filtered timeline, sparse timeline, `profile.json`, derived user-turn JSON, `**question_full_timeline_indices.json`** (input to the sparse remap script only). |
| `data/evaluation/`                            | **Evaluation:** canonical `**question_sparse40.json`** + `**user_turn_mixed.json**`.                                                                                                        |


Scripts default to these directories (see `ESL-Bench/scripts/*.py`).

---

## Source

- **File:** `data/construction(2026-04)/user110_AT_demo/timeline.json`
- **Scope:** Full multi-year timeline (`entry_count` 74,177).

## Step 1 — Year 2026 truncation (`timeline_26.json`)

**Goal:** Restrict the timeline to calendar year 2026 for a smaller, time-bounded slice.

**Rule:** Keep an entry if and only if the `time` field is an ISO timestamp whose year prefix is `2026` (string prefix `2026`, same convention as per-year entry counts).

**Unchanged fields:** `user_id`, `generated_at` (copied from source).

**Updated field:** `entry_count` set to the length of the filtered `entries` array.

**Result:**


| Metric           | Value                                                         |
| ---------------- | ------------------------------------------------------------- |
| Output file      | `data/construction(2026-04)/user110_AT_demo/timeline_26.json` |
| Entries retained | 2,674                                                         |
| First `time`     | `2026-01-01T04:31:19`                                         |
| Last `time`      | `2026-02-15T04:31:19`                                         |
| `entry_type` mix | `device_indicator`: 2,668; `event`: 6                         |


**Not included by this rule:** Entries whose `time` falls outside 2026 even if `end_time` spans into 2026 (truncation is by `time` only).

---

## Step 2 — Requirement-aligned indicator filter (`timeline_26_filtered.json`)

**Goal:** Drop wearable `device_indicator` rows that duplicate or weakly support the information domains in `requiement.txt` (personal health record: vitals targets, lifestyle, sleep, exercise, weight/glucose/BP context, stress/symptom-adjacent signals). **All non–`device_indicator` entries are kept** (e.g. `event` rows for care habits, diet, health events).

**Input:** `timeline_26.json` (under construction dir above).

**Rule:**

- If `entry_type != "device_indicator"` → **keep**.
- If `entry_type == "device_indicator"` → **keep** only when `indicator` is in the allowlist below (47 names).

**Updated field:** `entry_count` recomputed after filtering.

**Top-level schema:** Same as upstream (`user_id`, `generated_at`, `entry_count`, `entries`).

**Result:**


| Metric                  | Value                                                                  |
| ----------------------- | ---------------------------------------------------------------------- |
| Output file             | `data/construction(2026-04)/user110_AT_demo/timeline_26_filtered.json` |
| Entries after filter    | 2,168                                                                  |
| `event` rows            | 6 (unchanged)                                                          |
| `device_indicator` rows | 2,162 (was 2,668)                                                      |
| Rows removed            | 506 (11 indicator types × per-day bundles)                             |


### Allowlist — retained indicators (47)

`24-HourAverageDiastolicBP`, `24-HourAverageSystolicBP`, `FastingBloodGlucose-FBG`, `MeanGlucose-MG`, `BodyWeight`, `BodyMassIndex-BMI`, `BodyFatPercentage-BFP`, `VisceralFatLevel-VFL`, `MuscleMass`, `DailyExerciseDuration`, `DailyStepCount`, `DailyDistance`, `DailyAverageMETs`, `ActiveCalories`, `TotalDailyEnergyExpenditure-TDEE`, `LightActivityTime`, `ModerateActivityTime`, `VigorousActivityTime`, `SedentaryTime`, `StandingTime`, `LyingTime`, `ModerateExerciseMinutes`, `VigorousExerciseMinutes`, `AverageHeartRateDuringExercise`, `MaximumHeartRateDuringExercise`, `HeartRateRecovery-1Minute`, `RestingHeartRate-RHR`, `HeartRateVariability-HRV`, `VO2Max-Estimated`, `CardiorespiratoryFitness-CRF`, `TrainingLoad`, `TrainingReadiness`, `RecoveryTime`, `TotalSleepTime-TST`, `DeepSleepDuration`, `LightSleepDuration`, `REMDuration`, `SleepEfficiency-SE`, `SleepLatency-SL`, `NumberofAwakenings-NA`, `WakeAfterSleepOnset-WASO`, `LowestSpO2`, `MeanSpO2`, `SleepRecoveryScore`, `DailyStressScore`, `BodyBattery`, `BasalBodyTemperature-BBT`

### Removed indicator types (11)

`ActiveEnergy`, `ActivityEnergyExpenditure-AEE`, `BasalMetabolicRate-BMR`, `DeepSleepPercentage`, `LightSleepPercentage`, `MaximumHeartRate-HRmax`, `REMPercentage`, `RMSSD-RootMeanSquareofSuccessiveDifferences`, `TotalCaloriesBurned`, `TrainingEffect-Aerobic`, `TrainingEffect-Anaerobic`

---

## Step 3 — Per-day sparse device rows (`timeline_26_filtered_sparse40.json`)

**Motivation:** With a fixed 47-indicator allowlist, a naive daily digest can read like the **same template every day**. The bench instead **drops a random fraction of each day’s `device_indicator` rows** (default **40%**, RNG seed e.g. **42**) so the visible device lines **vary across days**, while **never** dropping an `entries` index referenced in the QA `ground_truth` (see `scripts/sparse_timeline_drop_indicators.py`).

**Inputs:** `timeline_26_filtered.json` + `**question_full_timeline_indices.json`** (same 120 questions as the final set, but `ground_truth` timeline paths still point at `**timeline_26_filtered.json**` indices).

**Outputs:**

- `data/construction(2026-04)/user110_AT_demo/timeline_26_filtered_sparse40.json` — sparse `entries`; `event` rows kept.
- `data/evaluation/question_sparse40.json` — same questions with `**ground_truth` remapped** to `**timeline_26_filtered_sparse40.json`** and new indices.

**Canonical bench line:** evaluation uses `**question_sparse40.json`** together with the **sparse** timeline file above. There is **no** `data/evaluation/question.json`; full-index questions live only as `**question_full_timeline_indices.json`** under construction for **regenerating** the sparse pair.

---

## Evaluation bundle — `data/evaluation/`

The `**bench/*.py` drivers** load evaluation JSON from `**bench/ESL-Bench/data/evaluation/`** by default (`question_sparse40.json`, `user_turn_mixed.json`). If you also keep copies under `data/evaluation/` (as in the table below), treat one location as canonical for edits and sync or symlink as needed.

### `question_sparse40.json`

- **Role:** Canonical QA file (120 items): **20** profile + **100** timeline questions.
- `**ground_truth`:** Profile paths resolve into `data/construction(2026-04)/user110_AT_demo/profile.json`. Timeline paths use `**timeline_26_filtered_sparse40.json.entries[…]`** and must be checked against that file under construction.
- **Answers:** In the `answer` field (scalar, string, structured JSON, or `null`).

### `user_turn_mixed.json`

- **Role:** Mixed user-turn transcript for evaluation. Timeline turns should read like natural user input.
- **Build:** Profile turns from `user_turn_profile.json` interleaved with daily timeline turns from `user_turn_timeline.json` (seed **42**, chronological days); see prior steps in this file. **Strict alignment** with sparse QA means `user_turn_timeline` should be produced from `**timeline_26_filtered_sparse40.json`** (re-run `populate_timeline_user_turns_llm.py` on that input when refreshing the bench).
- **Timeline prompt contract:** For each calendar day, `populate_timeline_user_turns_llm.py` sends the day's sparse timeline rows to the LLM and asks for **one or more natural first-person user messages** (the script does not enforce a fixed count). The script also reads `**question_sparse40.json`** and marks same-day timeline entries referenced by QA `ground_truth` as **required QA facts**. The generated user messages must explicitly include those answers (for example, the `2026-01-10` recovery-time answer `35.9616 hours`) and use other same-day rows as realistic noise.

**Sanity check:** `scripts/verify_question_ground_truth_in_user_turns.py` (defaults: `question_sparse40.json` + `user_turn_mixed.json` under evaluation, profile and timeline JSON under construction).

---

## Nanobot bench runtime (`bench/`)

This section summarizes how the **driver scripts** turn ESL-Bench JSON into **nanobot** subprocess calls, where artifacts land, and how **scoring** works. Script paths are relative to the **nanobot-health** repo root; evaluation JSON used by the drivers lives under `**bench/ESL-Bench/data/evaluation/`** (not only the historical `data/evaluation/` layout described in the tables above).

### What gets built upstream

The construction steps earlier in this document produce a **sparse timeline**, `**profile.json`**, `**question_sparse40.json**` (120 items with `ground_truth` + `answer`), and **user-turn transcripts** (e.g. `user_turn_mixed.json` or `user_turn.json`). Those files define *what* the agent should know and *what* we ask in QA. The `bench/*.py` layer does not regenerate them; it **replays** turns and **asks** questions through nanobot.

### Feeding nanobot (mechanism)

Every user or question string is sent the same way: a subprocess from repo root:

`python -m nanobot agent -m "<message>" --session <session_id> [--workspace DIR] [--config FILE] --no-markdown`

Optional environment per call:

- `**NANOBOT_CURRENT_TIME`** — set from `bench/esl_turn_timing.py` so ingest turns see a consistent simulated clock (merge-day profile, per-day steps, etc.).

So the bench does not link nanobot as a Python library; it drives the **CLI** the same way a human would, with a fixed **session** so later turns see prior context.

### Driver scripts


| Script                 | Role                                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `**run_agent.py`**     | Replays **all** `user` lines from a mixed JSON (default `user_turn_mixed.json`) in order, one `nanobot agent -m` per message. Optionally appends to `<run-result-dir>/nanobot_replies.jsonl`.                                                                                                                                                                        |
| `**eval_agent.py`**    | For each row in `**question_sparse40.json**`, sends the **question** string as a single `nanobot agent -m` turn on the **same session** when driven by `run_esl_bench.py`. Writes `**eval_results.json`** and extends `**nanobot_replies.jsonl**`. Optional `**--ingest-mixed**` replays the mixed file inside eval (only if you are *not* using `run_agent` first). |
| `**run_esl_bench.py*`* | Runs `**run_agent.py**` then `**eval_agent.py**` with the **same trailing argv** and a **shared** `--session` (unless you pass `--session` once, or override eval with `--eval-session`). Working directory for both children is the **repo root** (`nanobot-health/`).                                                                                              |


### Evaluation and scoring

After all QA turns complete, `**eval_agent.py`** scores replies:

- **Default (recommended):** If `**bench/.env`** defines `**LLM_MODEL_NAME**`, `**LLM_API_KEY**`, and `**LLM_API_BASE**`, scoring uses the **LLM judge** (`bench/llm_judge_eval.py`). It writes **one** file: `**eval_results.json`**, schema `**esl_eval_results_llm_judge_v1**`, with per-item `**llm_judge**` (`score_0_1`, `reasoning`, …). Top-level `**passed**` / `**pass_rate**` match the final per-item `**pass**` (LLM verdict). If a **single judge HTTP/parse** call fails, that item falls back to `**bench/heuristic_score.py`** (`pass_source=heuristic_fallback`).
- **Heuristic-only:** Pass `**--no-llm-judge`**, or omit LLM env vars. Then `**scoring_mode**` is `**heuristic_only**` and schema `**esl_eval_results_v1**`.
- **Legacy duplicate path:** `**--also-write-eval-results-llm-judge`** writes the same LLM payload again as `eval_results_llm_judge.json`.

**Offline utilities:** `uv run python bench/llm_judge_eval.py --migrate-format-only` rewrites an existing LLM eval JSON (no API). Defaults: `--input` / `--output` → `bench/run_result/eval_results.json`.

### Typical artifact layout

With `**--run-result-dir bench/run_result`** (or your own directory):

- `**nanobot_replies.jsonl**` — one JSON per assistant reply (ingest + QA lines).
- `**eval_results.json**` — full eval summary (LLM or heuristic).

By default `**eval_agent**` truncates `**nanobot_replies.jsonl**` and `**eval_results.json**` at the start of a run (unless `**--append-run-results**`). `**--no-run-result**` disables all run-result files.

Use `**--fresh-run-results**` on `**run_agent**` so a new bench run starts with an empty JSONL (when combined via `run_esl_bench`, pass it on the shared tail so both phases see it where applicable).

### End-to-end invocation (order and parameters)

1. `**run_esl_bench.py**` (preferred one-shot):
  ```bash
   cd /path/to/nanobot-health
   uv run python bench/run_esl_bench.py \
     --workspace /path/to/nanobot/workspace \
     --mixed-json bench/ESL-Bench/data/evaluation/user_turn.json \
     --run-result-dir bench/run_result \
     --fresh-run-results
  ```
  - `**--workspace**` — nanobot workspace (forwarded to both phases).
  - `**--mixed-json**` — mixed user-turn file for **ingest** (`run_agent`); `**eval_agent`** uses it only for default **QA clock** (`NANOBOT_CURRENT_TIME` derived from the file unless `--current-time` is set).
  - `**--run-result-dir`** — result folder for JSONL + `eval_results.json`.
  - `**--fresh-run-results**` — truncate `nanobot_replies.jsonl` at the start of ingest (see `run_agent.py`).
  - `**--session**` — optional; if omitted, bench generates e.g. `bench:esl:run:<uuid>` for **both** phases.
  - `**--eval-session`** — `run_esl_bench` only: use a different session id for **eval** than for ingest.
  - `**--stop-on-run-fail`** — if ingest exits non-zero, skip eval.
2. **Manual two-step** (same session id required if you want shared memory):
  ```bash
   uv run python bench/run_agent.py --session bench:esl:demo --workspace … \
     --mixed-json … --run-result-dir bench/run_result --fresh-run-results
   uv run python bench/eval_agent.py --session bench:esl:demo --workspace … \
     --questions-json bench/ESL-Bench/data/evaluation/question_sparse40.json \
     --mixed-json … --run-result-dir bench/run_result
  ```

---

## Reference

- Requirement text used for semantic alignment: `nanobot-health/requiement.txt` (filename as in repo).

## File map


| File                                                                             | Description                                                                                               |
| -------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `data/construction(2026-04)/user110_AT_demo/timeline.json`                       | Full timeline (all years).                                                                                |
| `data/construction(2026-04)/user110_AT_demo/timeline_26.json`                    | 2026-only by `time`.                                                                                      |
| `data/construction(2026-04)/user110_AT_demo/timeline_26_filtered.json`           | 2026 slice + indicator allowlist filter.                                                                  |
| `data/construction(2026-04)/user110_AT_demo/timeline_26_filtered_sparse40.json`  | Sparse per-day device rows + remap target for QA.                                                         |
| `data/construction(2026-04)/user110_AT_demo/question_full_timeline_indices.json` | Full-timeline-index question set (**input** to sparse script only).                                       |
| `data/construction(2026-04)/user110_AT_demo/profile.json`                        | Structured profile.                                                                                       |
| `data/construction(2026-04)/user110_AT_demo/user_turn_profile.json`              | NL user turns from profile.                                                                               |
| `data/construction(2026-04)/user110_AT_demo/user_turn_timeline.json`             | Daily natural user turns generated from sparse timeline rows, with QA-required facts plus same-day noise. |
| `data/evaluation/question_sparse40.json`                                         | **Canonical** bench questions + `ground_truth` + `answer` (sparse timeline indices).                      |
| `data/evaluation/user_turn_mixed.json`                                           | Mixed profile + timeline user transcript for evaluation.                                                  |
| `bench/construction.md`                                                          | This file: data construction log + bench runtime (nanobot drivers).                                       |



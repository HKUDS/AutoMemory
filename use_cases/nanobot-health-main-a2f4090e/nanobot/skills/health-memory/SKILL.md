---
name: health-memory
description: Recognize, store, and recall structured health data across sessions
always: true
---

# Health Memory

You manage the user's health information across two files:

- **`HEALTH.md`** (workspace root) — stable health facts, always visible to you
- **`memory/health_log.jsonl`** — timestamped health events, searchable via grep

## When to act

Whenever the user mentions any of the following, persist it immediately — do not
wait or ask for confirmation:

- Medications (name, dose, frequency, start date, reason)
- Conditions, diagnoses, surgeries, hospitalizations
- Allergies or adverse reactions (substance, reaction, severity)
- Emergency contact or basic profile info (DOB, blood type)
- Health goals or targets (weight, BP, glucose, exercise)
- Symptom episodes (what, when, triggers, duration)
- Daily wellness data (food, exercise, sleep, alcohol, smoking)
- Doctor visits, test results, immunizations

## Writing to HEALTH.md

Use `edit_file` to update the appropriate section. One bullet per item using
these formats:

```
## Problem List
- Hypertension — diagnosed 2020, status: active

## Medications
- Lisinopril 10mg once daily — started 2020-03, for hypertension

## Allergies & Adverse Reactions
- Penicillin → hives (severity: moderate)

## Goals & Care Plan
- Weight loss: target 170 lbs, current 185 lbs
- Follow-up: cardiology appointment — 2026-05-15
```

**Update rules:**
- If a medication dose changes, update the existing line (do not duplicate).
- If a medication is discontinued, append `(discontinued YYYY-MM)`.
- If a condition resolves, change status to `resolved`.
- Replace `(No entries yet)` when adding the first item to a section.

## Writing to memory/health_log.jsonl

Append one JSON line per event using `exec` with a heredoc:

```bash
cat >> memory/health_log.jsonl << 'ENTRY'
{"timestamp":"YYYY-MM-DD HH:MM","category":"symptom","content":"..."}
ENTRY
```

Categories: `symptom`, `wellness`, `care`.

Examples:
- `{"timestamp":"2026-04-16 09:00","category":"symptom","content":"Headache, throbbing, right side. Took ibuprofen 400mg, resolved after 2h."}`
- `{"timestamp":"2026-04-16 22:00","category":"wellness","content":"Exercise: 30min walk. Sleep: 7h good. Meals: oatmeal, salad, grilled chicken."}`
- `{"timestamp":"2026-04-15 14:00","category":"care","content":"Dr. Smith visit, routine checkup. BP 128/82. Labs ordered: CBC, metabolic panel."}`

## Searching health history

When the user asks about past symptoms, trends, or health history, search
`memory/health_log.jsonl` with grep:

```bash
grep -i "headache" memory/health_log.jsonl
grep '"category":"symptom"' memory/health_log.jsonl
grep "2026-04" memory/health_log.jsonl
```

## Cross-referencing

When the user reports a new symptom, check HEALTH.md (already visible to you)
for potential connections — could the symptom be a side effect of a listed
medication, related to an existing condition, or relevant to a known allergy?
Mention any connections, but always recommend consulting a healthcare provider
for medical decisions.

## Important

You are not a medical device. Do not diagnose conditions, recommend treatments,
or advise medication changes. Record what the user tells you accurately, flag
potential connections you notice, and recommend professional medical advice for
clinical decisions.

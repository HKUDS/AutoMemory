# note.md

## User Requirements

The user wants nanobot to remember all their health state across sessions. Specific categories from requiement.txt:

1. **Basic profile & emergency info** — age/DOB, emergency contact, blood type
2. **Problem list** — chronic conditions, past illnesses, surgeries/hospitalizations
3. **Medication list** — Rx drugs, OTC drugs, vitamins/supplements, dose/frequency/start date
4. **Allergies & adverse reactions** — allergen, reaction, severity
5. **Recent care timeline** — doctor visits, test/screening results, immunizations
6. **Symptom log** — symptoms, onset, triggers, duration
7. **Daily lifestyle/wellness log** — food, alcohol, smoking/vaping, exercise, sleep
8. **Goals & care plan** — weight targets, BP/glucose targets, rehab/exercise goals, medication adherence, follow-up reminders

Key insight: categories 1-4 are **stable/slowly-changing** facts. Categories 5-7 are **temporal/log-like** events. Category 8 is **goal-tracking** (stable but with progress updates).

## Agent Observations

- **Entry point**: `nanobot.cli.commands:app` (Typer). CLI `agent` command runs `process_direct`.
- **Architecture**: channels → MessageBus → AgentLoop → ContextBuilder + SessionManager → AgentRunner → LLM Provider.
- **Model**: minimax/minimax-m2.7 via OpenRouter.
- **Context window**: 65536 tokens. `maxTokens`: 8192.
- **Config**: `~/.nanobot/config.json`. Workspace: `~/.nanobot/workspace`.
- **Session**: JSONL in `workspace/sessions/<key>.jsonl`. Session key for CLI: `cli:direct`.
- **System prompt assembly**: `ContextBuilder.build_system_prompt` → identity template + bootstrap files (AGENTS.md, SOUL.md, USER.md, HEALTH.md, TOOLS.md) + MEMORY.md + always-on skills + skills summary + recent unprocessed history from history.jsonl.

## Discoveries

- Agent given health info → writes to HEALTH.md with structured sections.
- Agent given symptom → writes to `memory/health_log.jsonl` as structured JSONL.
- Agent cross-references symptoms against medications and conditions from HEALTH.md.
- The `health-memory` skill (always=true) teaches the agent the exact format for both files.
- Dream prompts enhanced with `[HEALTH-PROFILE]`, `[HEALTH-SYMPTOM]`, `[HEALTH-WELLNESS]`, `[HEALTH-CARE]`, `[HEALTH-GOAL]` tags.

## Implementation Details

### What was built

**Files modified in nanobot core:**
1. `nanobot/agent/context.py` — Added `HEALTH.md` to `BOOTSTRAP_FILES` list
2. `nanobot/agent/memory.py` — Added `health_file`, `health_log_file` attrs to MemoryStore; added `read_health()`, `write_health()`, `append_health_log()`, `read_health_log()`, `search_health_log()` methods; added `HEALTH.md` to GitStore tracked files; updated Dream `_build_tools()` to allow writing to `memory/` dir; updated Dream `run()` to include HEALTH.md in file_context
3. `nanobot/templates/agent/dream_phase1.md` — Added health-specific extraction tags
4. `nanobot/templates/agent/dream_phase2.md` — Added HEALTH.md and health_log.jsonl write paths
5. `nanobot/templates/agent/consolidator_archive.md` — Added health data preservation guidance

**Files created in workspace:**
1. `~/.nanobot/workspace/HEALTH.md` — Structured health profile template
2. `~/.nanobot/workspace/skills/health-memory/SKILL.md` — Always-on skill teaching the agent health data format

**Refine adapter (`nanobot_memory/memory/`):**
1. `__init__.py` — Standard `init()`, `memorize()`, `recall()` interface
2. `consolidation.py` — LLM-driven health extraction using litellm (reuses nanobot's OpenRouter endpoint)
3. `prompts.py` — System/user prompts for health memory extraction
4. `test_health_memory.py` — 9 functional tests (all pass), using real LLM

### How it differs from the design

- **Minimal deviations**: Implementation closely follows design document.
- **Dream write_file scope**: Extended `WriteFileTool` in Dream to allow writing to `memory/` directory (via `extra_allowed_dirs`) for `health_log.jsonl`. The design said Dream should write to health_log.jsonl but didn't specify how — this was the natural extension point.
- **Allergy data in USER.md**: Pre-existing test data from design phase left allergy info in USER.md. The new system puts allergies in HEALTH.md going forward. Both are in context so there's no data loss.
- **Recent Care Timeline**: Added a `## Recent Care Timeline` section to HEALTH.md template beyond what the design specified — this maps directly to user requirement #5.

### Integration method

- **Bootstrap files** (extension mechanism): Added `HEALTH.md` to `ContextBuilder.BOOTSTRAP_FILES` — the existing pattern for always-in-context files.
- **Skills** (extension mechanism): Created `skills/health-memory/SKILL.md` with `always: true` — the existing pattern for always-loaded skills.
- **Dream prompts** (template modification): Extended `dream_phase1.md` and `dream_phase2.md` templates.
- **Consolidator prompt** (template modification): Extended `consolidator_archive.md` template.
- **MemoryStore** (core extension): Added health file methods — natural extension of existing pattern.

### Test results

All 9 tests pass with real LLM (OpenRouter minimax-m2.7):
- TestInit: 3 tests (directory creation, preservation, re-init)
- TestMemorizeRecall: 5 tests (medication, allergy, symptom log, dedup, no-match)
- TestLifecycle: 1 test (init-use-reinit persistence)

### Agent verification

Tested 3 agent interactions after integration:
1. Shared diabetes diagnosis + metformin → wrote to HEALTH.md Problem List and Medications
2. Reported headache symptom → wrote to health_log.jsonl; cross-referenced against lisinopril and diabetes
3. Asked "what medications am I taking?" → correctly recalled all medications and conditions from HEALTH.md

## Known Weaknesses

- health_log.jsonl relies on keyword grep — no semantic search
- Daily wellness logging could grow health_log.jsonl large (compaction not yet automated)
- Dream runs every 2h — agent must directly write health data for immediate persistence
- No automatic drug interaction checking — agent can only flag obvious connections

## Open Questions

- Should health_log.jsonl compaction be implemented as a Dream extension or separate cron job?
- Should we migrate the penicillin allergy from USER.md to HEALTH.md automatically?

---

## Refine Summary

**Completed**: 3 phases (Phase 0 baseline + Phases 1–2 adversarial). Stopped at Phase 2 via exit condition 1 (Critic cannot break it after two escalations).

**Traces produced**: 9 total — 3 baseline (Phase 0), 2 escalation (Phase 1), 4 escalation (Phase 2).

### Phase 0 → Critic found:
- **Natural-language date recall failure**: health_log.jsonl stores ISO timestamps (`2026-04-15`) but users query by `"April 15"`. Keyword grep missed all date-anchored queries. Aggregate query_success = 0.778.

### Phase 1 → Improver fixed + Critic escalated:
- **Fix**: Added `date_human` field (e.g. `"April 15, 2026"`) to every log entry at write time; added ISO-normalization in `recall()` for backward-compatible matching of existing entries. Both propagated to `nanobot/agent/memory.py`.
- **Critic escalation**: Found paraphrase-mismatch — user says `"swollen ankles"`, LLM stores `"ankle swelling"`, substring `"swollen"` not found. Aggregate query_success = 0.967.

### Phase 2 → Improver fixed + Critic escalated twice → stop:
- **Fix**: Updated LLM extraction prompt to append `[user: <exact words>]` in health_log content fields, preserving user's original vocabulary. Added morphological root-stripping in `recall()` and `search_health_log()` as structural fallback.
- **Critic escalation 1** (traces 06–07): symptom resolution, lab values, care timeline precision, multi-step dose escalation, date-specific wellness recall → all 1.0/1.0.
- **Critic escalation 2** (traces 08–09): high-density supplement capture, implicit symptom mentions in non-health context, goal completion recognition, lab value trends → all 1.0/1.0.
- **Exit**: All 9 traces scored 1.0/1.0 after two escalations. Refine loop stopped.

### Files modified during refine:
- `nanobot_memory/memory/__init__.py` — date ISO normalization + morphological root search in `recall()`
- `nanobot_memory/memory/consolidation.py` — `_add_human_date()` adds `date_human` to log entries
- `nanobot_memory/memory/prompts.py` — prompt instruction to preserve `[user: <exact words>]` in health_log content
- `nanobot/agent/memory.py` — same `date_human` + morphological search propagated to production `append_health_log()` / `search_health_log()`

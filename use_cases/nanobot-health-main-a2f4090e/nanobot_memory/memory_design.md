# Memory Design: nanobot (Health State)

## Agent Summary

nanobot is a lightweight personal AI assistant built in Python with a Typer CLI,
message bus architecture, and multi-channel support. It uses OpenRouter (currently
minimax/minimax-m2.7) with a 65 536-token context window.

**Existing memory system (already functional):**

| Layer | File | Role |
|-------|------|------|
| Short-term | `sessions/<key>.jsonl` | Live conversation messages |
| Archive | `memory/history.jsonl` | Append-only LLM-summarized conversation chunks |
| Long-term stable | `USER.md` | User facts and preferences |
| Long-term stable | `SOUL.md` | Bot personality and tone |
| Long-term stable | `memory/MEMORY.md` | Project context and decisions |
| Version control | `.git/` (GitStore) | Tracks changes to long-term files |

**Memory flow:** When the conversation's token estimate exceeds the context window,
the `Consolidator` summarizes the oldest turns and appends them to `history.jsonl`.
Every 2 hours (or on `/dream`), the `Dream` processor reads new `history.jsonl`
entries and surgically edits `USER.md`, `SOUL.md`, and `MEMORY.md`. The agent can
also directly edit these files during conversation using file tools.

**System prompt assembly:** `ContextBuilder.build_system_prompt` concatenates:
identity template → bootstrap files (`AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`)
→ `MEMORY.md` → always-on skills → skills summary → recent unprocessed
`history.jsonl` entries.

**Key architectural properties:**
- Bootstrap files in `ContextBuilder.BOOTSTRAP_FILES` are always loaded into the
  system prompt — anything stored there is guaranteed to be seen every turn.
- The agent has full file tools (`read_file`, `edit_file`, `write_file`) and can
  directly write to workspace files during conversation.
- Dream uses `read_file`/`edit_file` tools for surgical edits, not full rewrites.
- `history.jsonl` is searchable via the agent's `grep` tool.
- The agent has a built-in `cron` service for scheduled tasks.

---

## Gap Analysis

### Gap 1: No structured health data model

**What information is lost/mishandled:** Health data is dumped into a generic
"Medical Information" section of `USER.md` as unstructured bullet points. There
are no dedicated sections for medications (with dose/frequency/start date),
allergies (with reaction type/severity), problem list, or emergency info.

**When:** At write time — when the agent or Dream stores health facts.

**Impact:** Difficult to update individual items (e.g., changing a medication dose
requires finding and editing a specific bullet). No enforcement of required fields
(e.g., allergy severity). Health data mixes with non-health user preferences.

**Root cause:** Missing capability — no health-specific schema or storage location.

### Gap 2: No temporal health data storage

**What information is lost:** Symptom episodes (what, when, duration, triggers),
daily wellness entries (food, exercise, sleep), doctor visits, test results, and
immunizations have no dedicated storage.

**When:** At session boundary and during consolidation — temporal health events
mentioned in conversation are summarized generically by the Consolidator, losing
specificity.

**Impact:** The user's symptom history, lifestyle patterns, and care timeline are
not accessible for trend analysis. The agent cannot answer "How has my blood
pressure been this month?" or "When was my last flu shot?"

**Root cause:** Missing capability — `USER.md` is for stable facts; `history.jsonl`
is for conversation summaries. Neither is designed for structured temporal health
events.

### Gap 3: Consolidator loses health-specific precision

**What information is lost:** Specific health measurements (BP: 140/90, A1c: 6.5%,
weight: 185 lbs), medication changes, and symptom details may be summarized away
during token-pressure consolidation.

**When:** At consolidation boundary — when old turns are archived to make room.

**Impact:** Precise health data that the user mentioned in passing may be
irrecoverably lost, even though it has clinical significance.

**Root cause:** Timing issue — health-relevant data should be extracted into
structured storage before or during consolidation, not after.

### Gap 4: Dream prompts are health-unaware

**What information is mishandled:** Dream's phase1 prompt extracts generic "atomic
facts" and "user preferences." Health data categories (medications with
dose/frequency, allergies with severity, symptoms with onset/duration) need
specialized extraction logic that understands what fields matter clinically.

**When:** During Dream phase1 analysis.

**Impact:** Health facts extracted by Dream may be incomplete (missing dose or
frequency), poorly categorized (allergy stored as a preference), or merged
incorrectly (two different medications combined into one entry).

**Root cause:** Missing capability — generic extraction prompts don't encode
health-domain knowledge.

### Gap 5: No health-context-aware recall

**What information is missed at recall time:** When the user reports new symptoms,
the agent doesn't proactively cross-reference the symptom against the medication
list (side effects), allergy list, or problem list (related conditions).

**When:** At recall time — during conversation when health context would be useful.

**Impact:** The agent responds to health queries in isolation rather than connecting
them to the user's full health picture. It might fail to flag that a reported
symptom matches a known medication side effect.

**Root cause:** Missing capability — no health-aware recall prompt that instructs
the agent to cross-reference health data.

### Gap 6: Daily logs would overwhelm always-in-context storage

**What information is at risk:** Daily food intake, exercise, sleep, alcohol, and
smoking logs are high-volume temporal data that cannot fit in a fixed-size
always-in-context file without eventually exceeding the token budget.

**When:** Over time, as daily entries accumulate.

**Impact:** If daily logs were stored in `USER.md` or a hypothetical `HEALTH.md`,
they would consume the entire system prompt budget within weeks.

**Root cause:** Representation issue — daily logs need append-only storage with
on-demand retrieval, not always-in-context injection.

### Gap 7: No health goal tracking or follow-up reminders

**What is missing:** The user wants weight loss targets, BP/glucose targets,
exercise goals, medication adherence goals, and follow-up reminders. There is
no mechanism to track progress toward goals or trigger reminder notifications.

**When:** Ongoing — goals are stated but never monitored.

**Impact:** The agent doesn't proactively remind the user about follow-ups, doesn't
track whether health goals are being met, and doesn't connect daily log entries
to goal progress.

**Root cause:** Missing capability — no integration between health goals and
nanobot's cron/heartbeat system, and no goal-progress tracking structure.

---

## Design Decisions

### Weight Class

**Light.** Nanobot is a lightweight file-based agent. The health memory enhancement
adds:
- One new structured markdown file (`HEALTH.md`) — always in context.
- One new JSONL file (`memory/health_log.jsonl`) — append-only, on-demand retrieval.
- Enhanced Dream prompts with health-specific extraction categories.
- One new skill (`skills/health-memory/SKILL.md`) teaching the agent how to
  structure health data.

No new databases, embedding stores, external dependencies, or additional LLM
calls beyond what Dream already makes. The design extends nanobot's existing
patterns rather than replacing them.

### Memory Types

#### Type 1: Health Profile (`HEALTH.md`)

**What it stores:** Stable, slowly-changing structured health facts organized into
fixed sections matching the user's requirements:

```markdown
# Health Profile

## Basic Info
- Date of birth: ...
- Blood type: ...
- Emergency contact: ... (name, relationship, phone)

## Problem List
- [condition] — diagnosed [date], [status: active/resolved], [notes]

## Medications
- [drug name] [dose] [frequency] — started [date], for [condition], [notes]

## Allergies & Adverse Reactions
- [allergen] → [reaction] (severity: mild/moderate/severe)

## Goals & Care Plan
- [goal]: target [value], current [value], deadline [date]
- Follow-up: [description] — [date]
```

**Write timing:** Immediately during conversation when the user shares health
facts (agent-triggered). Also during Dream consolidation when health facts are
extracted from `history.jsonl` (system-triggered).

**Write method:** LLM-driven intelligent extraction. The agent (or Dream) identifies
health facts from conversation, categorizes them, and writes structured markdown
with the specific fields listed above. The `health-memory` skill instructs the
agent on the exact format.

**Storage form:** `workspace/HEALTH.md` — structured markdown with fixed section
headers. Each entry is a single bullet point with standardized fields.

**Update semantics:** Merge with deduplication.
- New information is added to the appropriate section.
- Corrections overwrite existing entries (e.g., medication dose change → update
  the existing medication line, not add a duplicate).
- Medications that are discontinued are marked `(discontinued [date])` rather
  than deleted, preserving history.
- Problem list entries that resolve are marked `status: resolved`.
- Goals that are completed are marked with completion date and moved to a
  `## Completed Goals` section (or removed after Dream processes them).

**Recall timing:** Every LLM call (always-in-context).

**Recall method:** Loaded as part of system prompt by `ContextBuilder`. The file
is added to `BOOTSTRAP_FILES` so it is automatically injected alongside
`USER.md` and `SOUL.md`.

**Injection point:** System prompt, in the bootstrap files section. The agent
always sees the full health profile.

**Budget fit:** Target ~1500–2500 tokens. The health profile is bounded by real-world
data volume — a typical patient has 5–15 medications, 3–8 conditions, and a
handful of allergies. Even a complex patient's profile fits comfortably under
3000 tokens.

#### Type 2: Health Timeline (`memory/health_log.jsonl`)

**What it stores:** Timestamped health events organized into three categories:

1. **Symptom entries:** what symptom, when it started, triggers, what
   helped/worsened it, duration, resolution.
2. **Wellness entries:** daily food intake, exercise (type + duration), sleep
   (hours + quality), alcohol, smoking/vaping.
3. **Care timeline entries:** doctor visits (provider, reason, outcome), test
   results (test name, result, reference range), immunizations (vaccine, date).

```jsonl
{"timestamp":"2026-04-16 09:00","category":"symptom","content":"Headache started morning, throbbing, right side. Took ibuprofen 400mg, resolved after 2h."}
{"timestamp":"2026-04-16 22:00","category":"wellness","content":"Exercise: 30min walk. Sleep: 7h, good quality. Food: oatmeal breakfast, salad lunch, chicken dinner. No alcohol."}
{"timestamp":"2026-04-15 14:00","category":"care","content":"Dr. Smith visit — routine checkup. BP: 128/82. Labs ordered: CBC, metabolic panel."}
```

**Write timing:** During conversation when the user reports symptoms, wellness
data, or care events (agent-triggered). Also during Dream when temporal health
data is extracted from consolidated conversation summaries (system-triggered).

**Write method:** LLM-driven intelligent extraction. The agent identifies temporal
health data, categorizes it (symptom/wellness/care), and appends a structured
JSONL entry. The `health-memory` skill teaches the format.

**Storage form:** `memory/health_log.jsonl` — append-only JSONL. Each line is a
JSON object with `timestamp`, `category`, and `content` fields.

**Update semantics:** Append-only. Symptom resolutions are recorded as new entries
referencing the original (e.g., "Headache from 04/16 resolved after 2 days").
Test results that arrive later are new entries. No in-place edits.

**Recall timing:** On-demand, when the user asks about health history, symptom
patterns, or trends.

**Recall method:** The agent uses its built-in `grep` tool to search
`memory/health_log.jsonl` by keyword, category, or date range. Example:
`grep -i "headache" memory/health_log.jsonl` or
`grep '"category":"symptom"' memory/health_log.jsonl`.

**Injection point:** Tool result — grep output is returned as a tool call result
and enters the conversation as context for the agent's response.

**Budget fit:** Zero fixed context cost. Tokens are consumed only when the agent
retrieves entries via grep. A typical grep result returns 5–20 matching lines
(~500–2000 tokens), well within the tool result budget.

**Compaction:** When `health_log.jsonl` exceeds a configurable threshold (e.g.,
1000 entries or ~6 months), older wellness entries (daily food/exercise/sleep)
can be compacted into weekly summaries via an LLM call during Dream. Symptom
entries and care timeline entries are never compacted because they have
long-term clinical value.

### Write Method Summary

| Memory Type | Logic Style | Method |
|-------------|-------------|--------|
| Health Profile | Intelligent (LLM) | Dream phase1 enhanced with health-specific categories; agent's `health-memory` skill guides direct writes |
| Health Timeline | Intelligent (LLM) | Dream extracts temporal health events; agent appends directly during conversation |

**Dream enhancement for health extraction:**

Dream's phase1 prompt is extended with health-specific extraction categories:

```
Health-specific extraction — flag these with [HEALTH] and subcategory:
[HEALTH-PROFILE] stable health facts: conditions, medications (with dose/frequency),
  allergies (with reaction/severity), emergency contact, blood type
[HEALTH-SYMPTOM] symptom episodes: what, when, triggers, duration, resolution
[HEALTH-WELLNESS] daily logs: food, exercise, sleep, alcohol, smoking
[HEALTH-CARE] medical events: doctor visits, test results, immunizations
[HEALTH-GOAL] health targets: weight, BP, glucose, exercise goals, follow-up dates
```

Dream's phase2 prompt is extended to write `[HEALTH-PROFILE]` and `[HEALTH-GOAL]`
entries to `HEALTH.md`, and `[HEALTH-SYMPTOM]`, `[HEALTH-WELLNESS]`, and
`[HEALTH-CARE]` entries to `memory/health_log.jsonl`.

**Consolidator enhancement:**

The consolidator archive prompt is extended to prioritize preserving health-specific
data in its summaries:

```
When summarizing, preserve exact values for:
- Medications and doses
- Vital signs and lab results (with numbers)
- Allergy information
- Symptom descriptions with timing
```

### Recall Method Summary

| Memory Type | Logic Style | Method |
|-------------|-------------|--------|
| Health Profile | Rigorous | Always loaded into system prompt — no recall decision needed |
| Health Timeline | Intelligent (LLM) | Agent decides when to search based on conversational context |

### Recall Strategy

- **Health Profile (`HEALTH.md`):** Always-in-context. Every LLM call receives
  the full health profile as part of the system prompt.
- **Health Timeline (`health_log.jsonl`):** On-demand retrieval via grep. The
  agent's `health-memory` skill teaches it when and how to search.

### Write Actor and Timing

| axis | value |
|---|---|
| `retention_window` | `token_budget` (65 536-token window; `_snip_history` keeps a recent suffix, `Consolidator` archives oldest user-turn-aligned chunk to `history.jsonl` when the estimate exceeds half the window) |
| `write_actor` | `mixed` (agent writes `HEALTH.md` / `health_log.jsonl` immediately during conversation; Dream backfills from archived `history.jsonl`) |
| `write_timing` | `per_turn` for direct agent capture; Dream's per-cycle backfill is a secondary post-window enrichment |
| `recall_timing` | `HEALTH.md` always in context; `health_log.jsonl` retrieved on demand |

The agent-triggered per-turn path is primary because health data is
high-value and should persist immediately, not wait for Dream's 2-hour
cycle. Dream remains a secondary system-triggered backfill path for
content that was missed or only became available after consolidation.
`HEALTH.md` adds ~1500–2500 tokens to every system prompt, which at
65 536 total with 8192 max output tokens leaves ~55 000 tokens for
conversation history — a negligible impact.

---

## Gap Coverage

### Gap 1 (No structured health data model) → Health Profile (`HEALTH.md`)

`HEALTH.md` provides a structured schema with fixed sections for each health
category (profile, problems, medications, allergies, goals). The `health-memory`
skill teaches the agent the exact format. Dream's enhanced phase2 writes to
`HEALTH.md` with category-appropriate structure.

**Sufficient?** Yes — the fixed section headers enforce structure, and the
skill's format instructions ensure consistent entries. The merge/dedup
semantics handle updates cleanly.

**Edge cases:** A user with an unusually large medication list (20+) might
make `HEALTH.md` approach the 3000-token budget. This is acceptable for
real-world patient data; pathological cases can be handled by abbreviating
notes fields.

### Gap 2 (No temporal health data storage) → Health Timeline (`health_log.jsonl`)

`health_log.jsonl` provides dedicated append-only storage for symptoms,
wellness logs, and care timeline events. Each entry is timestamped and
categorized, enabling date-range and category-based retrieval.

**Sufficient?** Yes — the JSONL format matches nanobot's existing pattern
(`history.jsonl`), and the agent can search it with its built-in grep tool.

**Edge cases:** Very high-frequency logging (multiple wellness entries per day
over months) will grow the file. The compaction mechanism (Dream summarizes
old wellness entries into weekly summaries) mitigates this.

### Gap 3 (Consolidator loses health precision) → Enhanced consolidator prompt

The consolidator prompt is enhanced to explicitly preserve exact health values
(medication doses, vital signs, lab results) rather than summarizing them away.

**Sufficient?** Partially — this improves fidelity but doesn't guarantee
preservation. The primary mitigation is Gap 1 + Gap 2: health data is written
to structured storage (HEALTH.md, health_log.jsonl) during conversation,
before consolidation occurs. Consolidation is a backup path.

**Edge cases:** Health data mentioned in passing (e.g., "my BP was 130/85 at
the grocery store kiosk") might not be captured by the agent during
conversation. Enhanced consolidator prompt + Dream extraction provides the
backup path.

### Gap 4 (Dream prompts health-unaware) → Enhanced Dream phase1/phase2 prompts

Dream's phase1 prompt gains health-specific extraction categories
(`[HEALTH-PROFILE]`, `[HEALTH-SYMPTOM]`, etc.). Phase2 gains write paths for
`HEALTH.md` and `health_log.jsonl`.

**Sufficient?** Yes — Dream already has the `read_file`/`edit_file` tools and
the two-phase architecture handles analysis → action cleanly. Adding
health-specific categories is a prompt change, not an architecture change.

**Edge cases:** Dream processes in batches of 20 `history.jsonl` entries. If
a batch contains conflicting health data (e.g., user changed medication dose
twice), Dream should apply the most recent value. The phase2 prompt should
include temporal ordering guidance.

### Gap 5 (No health-context-aware recall) → Health Profile always-in-context + skill guidance

Because `HEALTH.md` is always in the system prompt, the agent always has the
full health profile available when responding to health queries. The
`health-memory` skill explicitly instructs the agent to cross-reference new
symptoms against the medication list and allergy list.

**Sufficient?** Partially — the agent can make connections if the information
is in context, but it's not a clinical decision support system. It won't have
drug interaction databases. The skill can instruct the agent to flag potential
concerns and recommend consulting a healthcare provider.

**Edge cases:** Temporal health data (from `health_log.jsonl`) is not always
in context. If the user asks about symptom trends, the agent must remember
to grep `health_log.jsonl`. The skill instructs this behavior.

### Gap 6 (Daily logs overwhelm always-in-context) → Separate storage in `health_log.jsonl`

Daily wellness entries go to `health_log.jsonl` (on-demand retrieval), not
`HEALTH.md` (always-in-context). Only stable summaries live in HEALTH.md
(e.g., "exercises 3x/week, typically running + yoga").

**Sufficient?** Yes — this cleanly separates high-volume temporal data from
low-volume stable facts, respecting the token budget.

**Edge cases:** None significant. The design naturally handles volume growth
through append-only storage + periodic compaction.

### Gap 7 (No goal tracking or reminders) → Goals section in `HEALTH.md` + cron integration guidance

Health goals are stored in `HEALTH.md` under a "Goals & Care Plan" section
with targets, current values, and deadlines. The `health-memory` skill
instructs the agent to:
1. Update goal progress when the user reports relevant data.
2. Use nanobot's built-in cron service to set follow-up reminders.

**Sufficient?** Partially — goal tracking (target vs. current value) is
handled by the structured section in `HEALTH.md`. However, automated
reminders depend on the cron service and gateway mode (reminders only fire
when the gateway is running). For CLI-only usage, the agent can mention
upcoming reminders reactively when the user checks in.

**Edge cases:** If the user has many active goals, the Goals section could
grow. This is naturally bounded by real-world goal count (typically 3–8).

---

## Open Questions

1. **Health log compaction policy:** Should old wellness entries be compacted
   into weekly summaries automatically, or only when the file exceeds a size
   threshold? Recommended: size threshold (1000 entries), with compaction
   running during Dream.

2. **HEALTH.md token budget cap:** Should there be an explicit cap (e.g.,
   3000 tokens)? If so, what happens when the cap is exceeded — truncation,
   archival of resolved conditions, or user notification? Recommended:
   soft cap with Dream cleaning resolved/old entries.

3. **Medical disclaimer:** Should the agent's health-memory skill include a
   disclaimer that it is not a medical device and cannot provide clinical
   advice? Recommended: yes, included in the skill's behavioral guidance.

4. **Privacy considerations:** Health data is sensitive (HIPAA/GDPR). Should
   `HEALTH.md` and `health_log.jsonl` have restricted file permissions or
   encryption? This is beyond memory design scope but worth flagging for
   implementation.

5. **Cron-based reminders:** Should the design specify a health-specific
   cron job template for follow-up reminders, or leave this to the agent's
   general cron capability? Recommended: leave to general cron; the skill
   teaches the agent how to set health reminders using existing cron tools.

---

## Appendix: File Layout

```
workspace/
├── AGENTS.md              # (existing) Agent instructions
├── SOUL.md                # (existing) Bot personality
├── USER.md                # (existing) General user preferences
├── HEALTH.md              # (NEW) Structured health profile — always in context
├── TOOLS.md               # (existing) Tool instructions
├── memory/
│   ├── MEMORY.md          # (existing) Project context
│   ├── history.jsonl      # (existing) Conversation summaries
│   ├── health_log.jsonl   # (NEW) Temporal health events
│   ├── .cursor            # (existing) Consolidator write cursor
│   ├── .dream_cursor      # (existing) Dream consumption cursor
│   └── .git/              # (existing) Version control
├── sessions/
│   └── *.jsonl            # (existing) Active conversations
└── skills/
    └── health-memory/
        └── SKILL.md       # (NEW) Health memory writing/recall instructions
```

## Appendix: HEALTH.md Template

```markdown
# Health Profile

## Basic Info
- Date of birth:
- Blood type:
- Emergency contact:

## Problem List
(No entries yet)

## Medications
(No entries yet)

## Allergies & Adverse Reactions
(No entries yet)

## Goals & Care Plan
(No entries yet)
```

## Appendix: Skill Summary (`health-memory`)

The `health-memory` skill instructs the agent to:

1. **Recognize health data** in conversation — medications, conditions,
   symptoms, allergies, vital signs, test results, lifestyle entries.
2. **Write to HEALTH.md** for stable facts using `edit_file` with the
   structured format (one bullet per item, standardized fields).
3. **Append to `memory/health_log.jsonl`** for temporal events using
   `write_file` (append mode) or the agent's file tools.
4. **Search `memory/health_log.jsonl`** when the user asks about health
   history, trends, or patterns — use `grep` with appropriate keywords.
5. **Cross-reference** new symptoms against the medication and allergy
   lists visible in `HEALTH.md` (always in context).
6. **Update goals** when the user reports relevant progress (e.g., new
   weight measurement → update weight goal's current value).
7. **Set reminders** using the cron tool for follow-up appointments and
   medication checks.
8. **Never provide clinical advice** — flag concerns and recommend
   consulting a healthcare provider.

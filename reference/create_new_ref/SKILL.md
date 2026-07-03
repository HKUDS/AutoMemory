---
name: create-new-ref
description: Systematically add a new memory-system reference to Auto_Memory's reference hub. Use when analyzing a new agent memory system, adding reference/<system>/details.md, saving prompts, or updating reference/hub/technique_map.json.
disable-model-invocation: true
---

# Create New Reference

Use this skill when adding a new memory system to `reference/` and `reference/hub/technique_map.json`.

The goal is not to summarize a repo. The goal is to distill reusable memory-system design moves that a coding agent can adapt to another agent.

## Output Contract

For a new system named `<system>`:

```text
reference/<system>/
├── details.md
└── prompts/
    └── <prompt-name>.md
```

Then append high-quality technique entries to `reference/hub/technique_map.json`.

Use existing systems as examples:

- `reference/zep/details.md`
- `reference/open-viking/details.md`
- `reference/MIRIX/details.md`
- `reference/lightgrep/details.md`
- `reference/template/details.md`
- `reference/template/technique_map_slot.json`

## Workflow

### 1. Inspect the Memory System

Read enough source/docs to identify production behavior:

- Where memory is written: lifecycle hook, tool call, session end, context-window pressure, explicit user request, batch job.
- What the memory units are: facts, events, observations, tool lessons, user profile, graph edges, summaries, cache entries.
- How memory is updated: append, merge, overwrite, invalidate, archive, delete, dedup.
- How memory is retrieved: always-in-context, keyword/BM25, vector candidate generation, graph traversal, hierarchical search, LLM scoring, reranking, on-demand drilldown.
- How memory is bounded: token limits, TTL, hotness, compaction, archival, retention windows.
- What is agent-specific: lifecycle integration, tool schemas, prompt format, storage constraints, latency budget.

Prefer implementation evidence over README claims. If prompts drive behavior, save the prompt text.

### 2. Write `details.md`

Follow the 8-section structure from `reference/template/details.md`:

1. **System architecture**: include an ASCII flow diagram and component responsibilities.
2. **Data model and schema**: memory types, fields, update semantics.
3. **Storage and indexing**: backend, indexes, retrieval unit, partitioning.
4. **Retrieval logic**: candidate generation, filtering, ranking/reranking, formatting.
5. **Store and update logic**: triggers, extraction, normalization, dedup, validation.
6. **Consolidation and lifecycle**: TTL, compaction, archival, conflict/staleness handling.
7. **Important implementation details**: key classes/functions and non-obvious engineering decisions.
8. **Transferability notes**: reusable parts, tightly coupled parts, best-fit agent/workflow.

Keep it implementation-oriented. Do not dump source files. Include line-level concepts and exact names of important classes/functions when useful.

### 3. Save Key Prompts

Put important LLM prompts in `reference/<system>/prompts/`.

Save prompts that define memory behavior:

- extraction/classification prompts
- dedup/merge/conflict prompts
- retrieval planning/ranking prompts
- compression/consolidation prompts
- router/meta-memory prompts
- sufficiency/validation prompts

Do not save generic chat prompts unless they directly control memory behavior.

### 4. Extract Techniques

Create technique entries only for reusable design moves. A valid technique must answer:

- What problem does this solve?
- What is the mechanism?
- When does it help?
- What does it cost?
- What kind of agent should borrow it?

Use categories from `reference/hub/technique_map.json`:

- `extraction`
- `dedup_conflict`
- `storage_organization`
- `retrieval_ranking`
- `lifecycle`
- `architecture`

Use philosophy tags:

- `weight`: `lightweight`, `medium`, or `heavy`
- `logic`: `rigorous`, `intelligent`, or both

### 5. Insight Quality Bar

Do not record obvious infrastructure as an insight.

Hard rule: **do not treat simple embedding/vector search as an insight.** A technique may mention vector search only when it is part of a richer mechanism, such as vector pre-filter + LLM dedup, hierarchical traversal, multi-signal reranking, hotness blending, or fallback selection.

Good techniques look like:

- lifecycle hook selection tied to agent events
- memory-type routing with different schemas and tools
- temporal invalidation instead of overwrite
- two-stage retrieval with sufficiency gates
- progressive disclosure with token-cost visibility
- L0/L1/L2 memory representations
- hotness-driven archival
- tool-execution observations as the memory source
- crash-safe redo logs for memory extraction
- prompt-cache-friendly session cursors

Reject techniques that are only:

- "uses embeddings"
- "stores in a vector database"
- "summarizes conversation"
- "uses SQLite/Postgres"
- "has a prompt"
- "uses RAG"

If the technique is common, only include it when the system has a specific adaptation, invariant, or engineering constraint that makes it transferable.

### 6. Add Technique Map Entries

Append entries matching `reference/template/technique_map_slot.json`:

```json
{
  "id": "<system>-<short-name>",
  "name": "<Technique name in plain English>",
  "source": "<system>",
  "category": "extraction",
  "summary": "<what it does and how>",
  "tradeoffs": "<cost vs benefit>",
  "when_to_use": "<scenario where it shines>",
  "philosophy": {
    "weight": ["medium"],
    "logic": ["intelligent"]
  },
  "detail_refs": [
    { "file": "<system>/details.md", "lines": [10, 25] }
  ],
  "prompt_refs": [
    "<system>/prompts/<prompt-name>.md"
  ]
}
```

Rules:

- `id` must be unique and stable.
- `source` must match the directory name under `reference/`.
- `detail_refs` must point to exact line ranges in `details.md`.
- `prompt_refs` should include only saved prompts that support this technique.
- `summary`, `tradeoffs`, and `when_to_use` should each be one concise sentence.
- Do not add a technique unless `details.md` contains enough evidence to support it.

### 7. Validate

Before finishing:

- Confirm `reference/<system>/details.md` exists and uses the 8-section structure.
- Confirm every `prompt_refs` path exists.
- Confirm every `detail_refs.file` exists and line ranges are meaningful.
- Confirm `technique_map.json` is valid JSON.
- Confirm the new techniques are not duplicates of existing entries.
- Confirm no real API keys, private keys, tokens, credentials, or `.env` files were added.
- Confirm no technique violates the simple-embedding rule.

If `ask_ref` is installed, smoke test:

```bash
ask_ref grep "<system>"
ask_ref cat "<new-technique-id>"
```

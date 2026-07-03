# Reference Hub

A curated catalog of 6 analyzed memory system implementations, distilled into
reusable design techniques. The build agent consults this hub during the
**Init** and **Refine** steps to borrow proven patterns — without copying
entire systems.

---

## What's Inside

### Analyzed Systems


| Directory      | System             | Architecture Style                                                                                       |
| -------------- | ------------------ | -------------------------------------------------------------------------------------------------------- |
| `zep/`         | **Graphiti** (Zep) | Temporal knowledge graph with bi-temporal edges, reflexion extraction, community aggregation             |
| `claude-mem/`  | **Claude Memory**  | Lightweight observation store with dual extraction (summary + observations), lifecycle hooks             |
| `MIRIX/`       | **MIRIX**          | Multi-tier meta-memory with router dispatching to 6 dedicated memory-type agents                         |
| `ama-agent/`   | **AMA-Agent**      | Causal memory graph with LLM relevance scoring and hierarchical compression                              |
| `open-viking/` | **Open-Viking**    | Intent-driven memory with 8-category extraction, L0/L1/L2 tiered representation, hotness-driven archival |
| `lightgrep/`   | **LightGrep**      | Minimal two-file system — always-in-context facts + grep-searchable event log                            |


Each system directory contains:

- `**details.md`** — Implementation-oriented deep dive covering architecture,
data model, storage, retrieval, lifecycle, and transferability notes.
- `**prompts/**` — Key LLM prompts used by the system (extraction, dedup,
consolidation, routing, etc.).

### Technique Catalog

All reusable techniques are indexed in `hub/technique_map.json` — a structured
registry of **29 techniques** across 6 categories:


| Category               | What It Covers                                        | # Techniques |
| ---------------------- | ----------------------------------------------------- | ------------ |
| `extraction`           | How to extract structured information from raw input  | 5            |
| `dedup_conflict`       | How to handle duplicates, contradictions, and updates | 3            |
| `storage_organization` | Backend choices, data model, and memory organization  | 3            |
| `retrieval_ranking`    | How to find, rank, and format relevant memories       | 9            |
| `lifecycle`            | TTL, compaction, archival, and consolidation          | 4            |
| `architecture`         | System structure, routing, integration patterns       | 5            |


Each technique entry includes:

- **Summary** — What it does and how
- **Tradeoffs** — Cost vs. benefit
- **When to use** — Scenario where this technique shines
- **Philosophy tags** — Weight (`lightweight` / `medium` / `heavy`) and logic
style (`rigorous` / `intelligent`)
- **Detail refs** — Pointers to specific line ranges in the source
`details.md`
- **Prompt refs** — Pointers to the actual LLM prompts used

---

## `ask_ref` CLI Tool

A CLI tool for browsing and querying the technique catalog, intended for both
human developers and the build agent.

### Install

```bash
pip install -e Auto_Memory/reference/hub/
```

### Commands

```bash
ask_ref ls                              # List all categories with technique IDs
ask_ref ls extraction                   # List techniques in a specific category
ask_ref grep "keyword"                  # Search by keyword across all fields
ask_ref grep "retrieval" -l rigorous    # Filter by logic style
ask_ref grep "tool" -c extraction       # Filter by category
ask_ref cat zep-reflexion               # Show full technique card with spliced source
ask_ref cat zep-reflexion --json        # JSON output for programmatic use
```

### How the Build Agent Uses It

During design and implementation, the agent queries `ask_ref grep` with
keywords describing the **problem to solve** (e.g., "tool output",
"compaction", "dedup", "eviction"). The catalog is small enough that
keyword search returns all relevant techniques regardless of weight class.
For example:

- Handling tool output capture →
`ask_ref grep "tool"`
- Needs dedup with intelligent logic →
`ask_ref grep "dedup" -l intelligent`

The agent reads the technique card and spliced source sections, then adapts the
pattern to the target agent — never copying wholesale.

---

## Adding a New Reference

For the full agent workflow, use [`create_new_ref/SKILL.md`](create_new_ref/SKILL.md).

1. Create `reference/<system-name>/details.md` following
  `template/details.md` (8-section structure).
2. Add key LLM prompts in `reference/<system-name>/prompts/`.
3. For each reusable technique, fill out a copy of
  `template/technique_map_slot.json`.
4. Append the technique entries to `hub/technique_map.json`.

---

## Directory Structure

```
reference/
├── README.md                           # This file
├── zep/                                # Graphiti temporal knowledge graph
│   ├── details.md
│   └── prompts/
│       ├── extract_nodes.md
│       ├── extract_edges.md
│       └── invalidate_edges.md
├── claude-mem/                         # Claude's lightweight memory
│   ├── details.md
│   └── prompts/
│       ├── observation_extraction.md
│       └── summary_extraction.md
├── MIRIX/                              # Multi-tier meta-memory router
│   ├── details.md
│   └── prompts/
│       ├── memory_component_overview.md
│       └── meta_memory_router.md
├── ama-agent/                          # Causal memory graph
│   ├── details.md
│   └── prompts/
│       ├── causal.md
│       ├── check_state_mem.md
│       ├── compress.md
│       ├── relevance_scoring.md
│       └── retrieve_sufficiency.md
├── open-viking/                        # Intent-driven memory
│   ├── details.md
│   └── prompts/
│       ├── dedup_decision.md
│       ├── intent_analysis.md
│       ├── memory_extraction.md
│       └── memory_merge.md
├── lightgrep/                          # Minimal grep-based recall
│   ├── details.md
│   └── prompts/
│       └── consolidation.md
├── hub/                                # CLI tool + technique index
│   ├── technique_map.json              # 29 techniques across 6 categories
│   ├── pyproject.toml
│   └── ask_ref/
│       ├── __init__.py
│       └── cli.py
├── create_new_ref/                     # Skill for adding a new reference
│   └── SKILL.md
└── template/                           # Template for new references
    ├── details.md
    ├── technique_map_slot.json
    └── prompts/
        └── prompt.md
```


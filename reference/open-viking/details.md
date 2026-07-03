# Details for OpenViking

Implementation-oriented reference for the OpenViking context database design.

---

## 1. System architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  session.commit() flow:                                           │
│    Archive messages → MemoryExtractor (LLM, 8 categories)         │
│    → Profile: always merge                                        │
│    → Tools/Skills: merge stats + LLM-merge guidelines             │
│    → Others: MemoryDeduplicator (vector + LLM → skip/create/none) │
│    → VikingFS.write_file() → Embed → Index                       │
├──────────────────────────────────────────────────────────────────┤
│  client.search() flow:                                            │
│    IntentAnalyzer → TypedQueries (MEMORY/RESOURCE/SKILL)          │
│    → HierarchicalRetriever: global vector → recursive dir search  │
│    → Hotness blend → Return MatchedContext (L0/L1; L2 on demand)  │
└──────────────────────────────────────────────────────────────────┘
```

**Components:** SessionCompressor (`session/compressor.py`), MemoryExtractor (`session/memory_extractor.py`), MemoryDeduplicator (`session/memory_deduplicator.py`), VikingFS (`storage/viking_fs.py`), HierarchicalRetriever (`retrieve/hierarchical_retriever.py`), IntentAnalyzer (`retrieve/intent_analyzer.py`), MemoryArchiver (`session/memory_archiver.py`). Backend: AGFS (local/S3) + VikingDB (vector index).

---

## 2. Data model and schema

**Context (`core/context.py`):** `uri`, `parent_uri`, `is_leaf`, `abstract` (L0), `context_type` (memory/resource/skill), `category`, `level` (0/1/2), `created_at`, `updated_at`, `active_count`, `owner_space`, `account_id`.

**Memory categories:**

| Category | Owner | Mergeable | URI pattern |
|----------|-------|-----------|-------------|
| profile | user | Yes (always) | `viking://user/{space}/memories/profile.md` |
| preferences | user | Yes | `.../memories/preferences/mem_xxx.md` |
| entities | user | Yes | `.../memories/entities/mem_xxx.md` |
| events | user | No | `.../memories/events/mem_xxx.md` |
| cases | agent | No | `viking://agent/{space}/memories/cases/mem_xxx.md` |
| patterns | agent | Yes | `.../memories/patterns/mem_xxx.md` |
| tools | agent | Yes | `.../memories/tools/{tool_name}.md` |
| skills | agent | Yes | `.../memories/skills/{skill_name}.md` |

**L0/L1/L2 files:** `{name}.abstract.md` (~100 tokens), `{name}.overview.md` (~2k tokens), `{name}.md` (full).

**ToolSkillCandidateMemory:** extends CandidateMemory with `tool_name`/`skill_name`, statistics (`call_time`, `success_time`, `avg_duration_ms`, `total_tokens`), metadata (`best_for`, `optimal_params`, `recommended_flow`, `key_dependencies`, `common_failures`, `recommendation`).

**Update semantics:** Profile always merges; preferences/entities/patterns support LLM merge; events/cases are create-or-delete only; tools/skills accumulate stats in code, merge guidelines via LLM.

---

## 3. Storage and indexing

- **Backend**: AGFS (abstract filesystem over local or S3); VikingDB for vector embeddings on L0 abstracts.
- **Chunking**: Long memories (> `memory_chunk_chars`) split into overlapping chunks; each chunk indexed separately as `uri#chunk_NNNN`.
- **Relations**: `.relations.json` per directory; bidirectional links between co-session memories/resources/skills.

---

## 4. Retrieval logic

1. **Query planning**: IntentAnalyzer → 0–5 TypedQueries (MEMORY/RESOURCE/SKILL) with priority 1–5.
2. **Hierarchical traversal**: Global vector search → priority queue of directories → recursive child search → `score = 0.5 * embedding + 0.5 * parent_score` → converge when top-k stable for 3 rounds.
3. **Hotness blend**: `final = (1 - 0.2) * semantic + 0.2 * hotness_score`.
4. **Output**: MatchedContext with L0/L1; L2 fetched only on explicit request.

---

## 5. Store and update logic

**Trigger:** `session.commit()` → archive messages → `MemoryExtractor.extract()` via `compression.memory_extraction` prompt.

**Category-specific paths:**
- **Profile**: skip dedup; always merge via `memory_merge_bundle` prompt.
- **Tools/Skills**: accumulate stats in Python; merge guidelines via LLM; one file per tool/skill.
- **Others**: MemoryDeduplicator → vector pre-filter (same category + owner_space) + batch-internal dedup → LLM `dedup_decision` → normalize (skip never carries actions; create+merge → none) → execute create/merge/delete.

**Crash safety**: Redo-log protects the extraction phase; resumes on restart.

---

## 6. Consolidation and lifecycle

- **Hotness**: `sigmoid(log1p(active_count)) * exp(-decay_rate * age_days)`, half-life 7 days, check interval 3600s.
- **Archival**: MemoryArchiver moves memories with hotness < 0.1 and age > 7 days to `{scope}/_archive/`; excluded from default retrieval, restorable.
- **Session compression**: On commit, archived messages get L0/L1 summaries via `structured_summary` prompt.

---

## 7. Important implementation details

- **Batch-internal dedup**: Tracks newly created memories within the same commit batch to avoid intra-batch duplicates.
- **Score propagation α = 0.5**: Child inherits half from parent directory score; enables directory-level relevance to guide recursive search.
- **Convergence = 3 rounds**: Hierarchical retrieval stops when top-k unchanged for 3 consecutive directory expansions.
- **Dedup normalization**: Code enforces that `skip` never carries actions, `create` can only carry `delete` (not `merge`), and `create+merge` in list normalizes to `none`.

---

## 8. Transferability notes

**Reusable:** L0/L1/L2 tiered representation; hierarchical directory retrieval with score propagation; hotness-driven archival; tool/skill stat tracking; vector pre-filter + LLM dedup pattern.

**Tightly coupled:** VikingFS/AGFS backend; VikingDB vector index; the 8-category taxonomy is opinionated.

**Best fit:** Long-running agents with diverse memory needs across many sessions, especially when structured organization and lifecycle management matter.

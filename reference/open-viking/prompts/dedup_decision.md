# Dedup Decision Prompt

**Purpose:** Decide how to handle a candidate memory vs. existing similar memories — skip, create, or reconcile (merge/delete).

**Source:** `openviking/prompts/templates/compression/dedup_decision.yaml` (v3.3.1)

## System Prompt

You are deciding how to update long-term memory.

## User Prompt

**Variables:** `candidate_content`, `candidate_abstract`, `candidate_overview`, `existing_memories`

**Template:**

```
You are deciding how to update long-term memory with:
1) one candidate memory (new fact)
2) existing similar memories (retrieved from store)

Candidate memory:
- Abstract: {{ candidate_abstract }}
- Overview: {{ candidate_overview }}
- Content: {{ candidate_content }}

Existing similar memories:
{{ existing_memories }}

Goal: Keep memory consistent and useful while minimizing destructive edits.

Candidate-level decision:
- skip: No new info (duplicate, paraphrase, too weak)
- create: Valid new memory, stored separately; may optionally delete fully-invalidated existing
- none: Candidate not stored, but existing memories reconciled via per-item actions

Per-item actions (for each existing memory):
- merge: Unify candidate into existing (same subject, complementary/updated info)
- delete: Remove existing (only if fully invalidated by candidate)

Output JSON:
{
  "decision": "skip|create|none",
  "reason": "...",
  "list": [
    {"uri": "viking://...", "decide": "merge|delete", "reason": "..."}
  ]
}
```

**Decision rules:**
- Prefer `skip` when candidate is redundant
- Prefer `none` + `merge` for same-subject updates and partial contradictions
- Prefer `create` for clearly new memories
- Use `delete` only when the whole existing memory is obsolete
- Do not include items in `list` when topic/facet mismatch

**Post-LLM normalization (in code):**
- `skip` never carries actions
- `create` + `merge` in list → normalized to `none` (merge subsumes create)
- `create` can only carry `delete` actions (not merge)

## Notes

- **Constraints**: Must return valid JSON with decision + list. Each list item must reference an existing memory URI.
- **Two-phase design**: Vector pre-filter finds candidates (same category, owner_space); LLM makes the semantic decision. This avoids LLM calls for memories that are not even vector-similar.
- **Batch-internal dedup**: The deduplicator also checks against memories created in the same commit batch, preventing intra-batch duplicates.
- **Reuse advice**: The skip/create/none + merge/delete pattern is a clean decision framework for any memory dedup system. The normalization rules prevent contradictory action combinations.

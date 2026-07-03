# Memory Merge Bundle Prompt

**Purpose:** Merge one existing memory with one new memory, producing all three levels (L0/L1/L2) in a single response.

**Source:** `openviking/prompts/templates/compression/memory_merge_bundle.yaml` (v1.0.0)

## System Prompt

You are merging one existing memory with one new memory update.

## User Prompt

**Variables:** `existing_abstract`, `existing_overview`, `existing_content`, `new_abstract`, `new_overview`, `new_content`, `category`, `output_language`

**Template:**

```
Category: {{ category }}
Target Output Language: {{ output_language }}

Existing memory:
- Abstract (L0): {{ existing_abstract }}
- Overview (L1): {{ existing_overview }}
- Content (L2): {{ existing_content }}

New memory:
- Abstract (L0): {{ new_abstract }}
- Overview (L1): {{ new_overview }}
- Content (L2): {{ new_content }}

Requirements:
- Merge into a single coherent memory
- Keep non-conflicting details from existing
- Update conflicting details to newer fact
- Output language: {{ output_language }}

Output JSON:
{
  "decision": "merge",
  "abstract": "merged L0...",
  "overview": "merged L1...",
  "content": "merged L2...",
  "reason": "..."
}
```

## Notes

- **Constraints**: Must return all three levels (L0/L1/L2) in a single JSON response.
- **Conflict resolution**: Newer fact wins for conflicting details; non-conflicting details preserved.
- **Usage contexts**: Used for profile (always merge), preferences/entities/patterns (when dedup decides merge), and tool/skill guidelines.
- **Simpler variant**: `compression.memory_merge` (v1.1.0) merges only the content field (L2) and returns plain text. Used when only a single level needs updating.
- **Reuse advice**: The three-level merge pattern ensures consistency across all representation layers. Adaptable to any tiered memory representation.

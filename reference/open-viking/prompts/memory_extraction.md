# Memory Extraction Prompt

**Purpose:** Extract memories from session messages into 8 categories with L0/L1/L2 three-level structure.

**Source:** `openviking/prompts/templates/compression/memory_extraction.yaml` (v5.2.0)

## System Prompt

You are a long-term memory extraction system. Analyze the conversation and extract personalized, long-term-valid, specific memories.

## User Prompt

**Variables:** `summary`, `recent_messages`, `user`, `feedback`, `output_language`

**Category decision table (8 categories):**

| Category | Owner | Trigger | Example |
|----------|-------|---------|---------|
| profile | user | Name, age, occupation, background, location, identity | "I'm a backend engineer at Google" |
| preferences | user | Likes, dislikes, habits, styles, tool preferences | "I prefer dark mode" |
| entities | user | People, organizations, projects, systems the user mentions | "My team uses Kubernetes" |
| events | user | Dated occurrences, milestones, deadlines | "We launched v2.0 last week" |
| cases | agent | Completed task with outcome, success/failure patterns | (auto-extracted from session) |
| patterns | agent | Recurring approaches, best practices learned | "User always wants tests first" |
| tools | agent | Tool usage data from `[ToolCall]` records | (auto-extracted from ToolPart) |
| skills | agent | Skill execution data from `[SkillCall]` records | (auto-extracted from ToolPart) |

**Three-level output per memory:**
- **L0 (abstract)**: One-line index entry (~100 tokens); used for vector search
- **L1 (overview)**: Structured Markdown summary (~2k tokens); used for reranking and navigation
- **L2 (content)**: Full narrative with all detail; loaded on demand

**Processing rules:**
- No relative temporal references (convert to absolute dates)
- Exhaustive extraction (high recall)
- One facet per preference memory
- Tools/Skills extracted from `[ToolCall]` / `[SkillCall]` records in messages

**Output format:**
```json
{
  "memories": [
    {
      "category": "preferences",
      "abstract": "User prefers 30s timeout for translate API",
      "overview": "## Preference\n- translate API timeout: 30s\n- Reason: default was too short",
      "content": "Full narrative..."
    },
    {
      "category": "tools",
      "tool_name": "translate_api",
      "abstract": "translate_api: 5 calls, 80% success, avg 2.1s",
      "overview": "## Tool Stats\n...",
      "content": "Full content...",
      "best_for": "...",
      "optimal_params": "...",
      "recommended_flow": "...",
      "key_dependencies": "...",
      "common_failures": "...",
      "recommendation": "..."
    }
  ]
}
```

**Tool/Skill additional fields:** `tool_name`, `skill_name`, `best_for`, `optimal_params`, `recommended_flow`, `key_dependencies`, `common_failures`, `recommendation`.

## Notes

- **Constraints**: Output must be valid JSON; each memory must have category + L0/L1/L2; tool/skill memories must include name and statistics.
- **Output language**: Follows `output_language` variable (auto-detected from session).
- **Failure modes**: If LLM returns invalid JSON, extraction retries; if `[ToolCall]` records absent, tool/skill categories are skipped.
- **Reuse advice**: The 8-category taxonomy and L0/L1/L2 structure are the core design; categories can be adapted. The tool/skill extraction from execution records is reusable for any tool-heavy agent.

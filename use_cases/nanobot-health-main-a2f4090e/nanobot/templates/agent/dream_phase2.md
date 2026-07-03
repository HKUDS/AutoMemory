Update memory files based on the analysis below.
- [FILE] entries: add the described content to the appropriate file
- [FILE-REMOVE] entries: delete the corresponding content from memory files
- [SKILL] entries: create a new skill under skills/<name>/SKILL.md using write_file
- [HEALTH-PROFILE] and [HEALTH-GOAL] entries: update HEALTH.md with structured health data
- [HEALTH-SYMPTOM], [HEALTH-WELLNESS], [HEALTH-CARE] entries: append to memory/health_log.jsonl

## File paths (relative to workspace root)
- SOUL.md
- USER.md
- HEALTH.md
- memory/MEMORY.md
- memory/health_log.jsonl
- skills/<name>/SKILL.md (for [SKILL] entries only)

Do NOT guess paths.

## Health data rules
- [HEALTH-PROFILE]: edit HEALTH.md using edit_file. Place entries under the matching section (Problem List, Medications, Allergies, Basic Info). Use format: `- [item] — [details]`. Replace `(No entries yet)` when adding first entry. If an item already exists, update it rather than duplicating.
- [HEALTH-GOAL]: edit HEALTH.md Goals section using edit_file. Format: `- [goal]: target [value], current [value]`.
- [HEALTH-SYMPTOM/WELLNESS/CARE]: append to memory/health_log.jsonl using write_file. Each entry is one JSON line: `{"timestamp":"YYYY-MM-DD HH:MM","category":"symptom|wellness|care","content":"..."}`. Read the existing file first if needed, then write the complete updated content.

## Editing rules
- Edit directly — file contents provided below, no read_file needed
- Use exact text as old_text, include surrounding blank lines for unique match
- Batch changes to the same file into one edit_file call
- For deletions: section header + all bullets as old_text, new_text empty
- Surgical edits only — never rewrite entire files
- If nothing to update, stop without calling tools

## Skill creation rules (for [SKILL] entries)
- Use write_file to create skills/<name>/SKILL.md
- Before writing, read_file `{{ skill_creator_path }}` for format reference (frontmatter structure, naming conventions, quality standards)
- **Dedup check**: read existing skills listed below to verify the new skill is not functionally redundant. Skip creation if an existing skill already covers the same workflow.
- Include YAML frontmatter with name and description fields
- Keep SKILL.md under 2000 words — concise and actionable
- Include: when to use, steps, output format, at least one example
- Do NOT overwrite existing skills — skip if the skill directory already exists
- Reference specific tools the agent has access to (read_file, write_file, exec, web_search, etc.)
- Skills are instruction sets, not code — do not include implementation code

## Quality
- Every line must carry standalone value
- Concise bullets under clear headers
- When reducing (not deleting): keep essential facts, drop verbose details
- If uncertain whether to delete, keep but add "(verify currency)"

# Observation Extraction Prompt

Structured extraction of observations from tool executions. Used by Claude Agent SDK when processing tool I/O.

**Source:** `src/sdk/prompts.ts`, `plugin/modes/code/mode.json` (or other mode configs)

## Schema (XML)

```xml
<observation>
  <type>[ decision | bugfix | feature | refactor | discovery | change ]</type>
  <title>Short title capturing core action</title>
  <subtitle>One sentence explanation (max 24 words)</subtitle>
  <facts>
    <fact>Concise, self-contained statement</fact>
  </facts>
  <narrative>Full context: what was done, how it works, why it matters</narrative>
  <concepts>
    <concept>knowledge-type-category</concept>
  </concepts>
  <files_read>
    <file>path/to/file</file>
  </files_read>
  <files_modified>
    <file>path/to/file</file>
  </files_modified>
</observation>
```

## Observation Types (code mode)

| Type       | Emoji | Description                    |
|------------|-------|--------------------------------|
| decision   | 🟤    | Architecture or design decision|
| bugfix     | 🔴    | Bug fixes and corrections     |
| feature    | 🟣    | New features or capabilities  |
| refactor   | 🔄    | Code refactoring and cleanup  |
| discovery  | 🔵    | Learnings about the codebase  |
| change     | ✅    | General changes               |

## Constraints

- Type must match mode.observation_types.
- Title should be specific, actionable, searchable.
- Facts: concise, self-contained; no dependencies on narrative.
- Concepts: tags for filtering (e.g., hooks, timeout, npm).

## Reuse Advice

- Modes are configurable (code, email-investigation, etc.); override observation_types and prompts.
- Use ModeManager for inheritance (e.g., code--ko extends code with language override).

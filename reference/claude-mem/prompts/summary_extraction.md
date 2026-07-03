# Session Summary Extraction Prompt

Progress summary at session stop. One summary per prompt_number.

**Source:** `src/sdk/prompts.ts` — `buildSummaryPrompt()`, mode.prompts.summary_*

## Schema (XML)

```xml
<summary>
  <request>Short title capturing user's request</request>
  <investigated>What has been explored so far?</investigated>
  <learned>What have you learned about how things work?</learned>
  <completed>What work has been completed? What has shipped?</completed>
  <next_steps>What are you planning to work on next?</next_steps>
  <notes>Additional insights or observations</notes>
</summary>
```

## Trigger

- **Stop hook** fires when Claude stops (user interrupts or task completes).
- Mode switch: "PROGRESS SUMMARY" — agent must output `<summary>` only, not `<observation>`.

## Storage

- Stored in session_summaries table.
- Linked to sdk_session_id, project, prompt_number.
- FTS5 indexed for search (request, investigated, learned, completed, next_steps, notes).

## Reuse Advice

- Summary provides session-level compression; observations provide granular tool-level memory.
- Context injection can show last summary + observation index.

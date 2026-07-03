# Consolidation Prompt

**Purpose:** Consolidate a chunk of conversation into a timestamped history entry and an updated long-term memory file. Uses forced tool calling to ensure structured output.

## System Prompt

```
You are a memory consolidation agent. Call the save_memory tool with your consolidation of the conversation.
```

## User Prompt

**Variables:** `current_memory` (existing MEMORY.md content), `formatted_messages` (conversation chunk)

```
Process this conversation and call the save_memory tool with your consolidation.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{formatted_messages}
```

**Message format:** Each message is rendered as:
```
[2026-03-15 14:30] USER: content here
[2026-03-15 14:31] ASSISTANT [tools: exec, write_file]: response here
```

## Tool Schema

```json
{
  "name": "save_memory",
  "description": "Save the memory consolidation result to persistent storage.",
  "parameters": {
    "type": "object",
    "properties": {
      "history_entry": {
        "type": "string",
        "description": "A paragraph summarizing key events/decisions/topics. Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search."
      },
      "memory_update": {
        "type": "string",
        "description": "Full updated long-term memory as markdown. Include all existing facts plus new ones. Return unchanged if nothing new."
      }
    },
    "required": ["history_entry", "memory_update"]
  }
}
```

## Notes

- **Forced tool choice**: `tool_choice={"type": "function", "function": {"name": "save_memory"}}` forces structured output. Falls back to `tool_choice="auto"` if the provider rejects forced mode.
- **Two outputs in one call**: `history_entry` goes to HISTORY.md (append); `memory_update` replaces MEMORY.md entirely.
- **Full replacement semantics**: The LLM must return the complete MEMORY.md content including existing facts, not just new additions. This enables implicit dedup and conflict resolution without a separate pipeline.
- **Grep-oriented history**: The `history_entry` description instructs the LLM to include detail useful for grep search — keywords, names, decisions — so future keyword searches are effective.
- **Failure mode**: If the LLM fails to produce a valid tool call, the system retries. After 3 consecutive failures, raw messages are dumped to HISTORY.md with a `[RAW]` marker.
- **Reuse advice**: The forced-tool-call pattern for structured consolidation output is portable to any provider supporting function calling. The two-output design (append-log + full-replacement-state) is a clean pattern for separating event history from accumulated knowledge.

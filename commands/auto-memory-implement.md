---
name: auto-memory-implement
description: Implement a memory system from an existing design document
argument-hint: agent-repo-path
alwaysApply: true
---

# auto-memory-implement

**Target agent repository**: $ARGUMENTS

## Prerequisites

Before running this command, `/auto-memory-design` must have completed,
producing:
- `<agent-repo>/<agent-name>_memory/memory_design.md`
- `<agent-repo>/<agent-name>_memory/worktraces/note.md`

## Goal

Implement the memory system described in `memory_design.md`, integrate it
into the agent, and prepare the project for refine.

**Important**: You are required to implement the ENTIRE memory design in
`memory_design.md`, DO NOT leave any gap to the future. Implementation 
should be as faithful to the design document as possible. If there is any gap 
cannot be fully fulfilled, you should explicitly document them under "known limitations".

## Tool

During your work, consult the reference system for proven patterns:
```
ask_ref ls                                   # list all categories
ask_ref ls extraction                        # list one category
ask_ref grep "keyword"                       # search by keyword
ask_ref grep "keyword" -l rigorous           # filter by logic style
ask_ref cat <technique-id>                   # full technique card
```

## Terminology

- Retention Window: the most recent K turns or tokens that directly visible 
  in the model input as context.
- Window Flush: the action of clearing the retention window, and sent all information
  in the window to the memory system with designed consolidation stragegy.
- Write Actor: the entity that triggers the write action, e.g. agent (call tools), 
  system (flush window), user (modify file, cli).

## What to Do

### 1. Read the Design

Read `<agent-repo>/<agent-name>_memory/memory_design.md` as the single
source of truth for what to build. Read `note.md` for additional context
(user requirements, agent observations, open questions).

### 2. Implement the Memory System

Build the memory system in whatever form fits the agent's architecture.
The implementation is **not** constrained to a fixed path or a fixed
interface. Let the design drive the shape:

- If the design calls for a standalone module, build a standalone module.
- If the design calls for tool registration, register tools.
- If the design calls for extending agent core code, extend it.
- If the design calls for a combination, build a combination.

### 3. Write Functional Tests

Write tests that exercise the memory system:
- Round-trip: write then read for each component.
- Deduplication / update semantics.
- Lifecycle: init, use, re-init with same data directory.
- Component integration (components that depend on each other).

Tests must use a real LLM provider when the memory system involves LLM
calls — no mocks.

### 4. Integrate into the Agent

Integrate the memory system into the agent. Prefer existing extension
mechanisms (tools, plugins, hooks, middleware) over patching core code.

Verify the agent still runs correctly after integration.

### 5. Initialize Refine State

Create `<agent-repo>/<agent-name>_memory/worktraces/refine_state.json`
using the template at `@Auto_Memory/scaffolds/refine_state_template.json`.

The initial state should have:
- `step_status`: `"ready"`
- `runner_path`: `""`
- `trace_registry`: `[]`
- `phase_history`: `[]`

This signals to the refine command that implementation is complete.

### 6. Write Implemented.md
Write the final implementation doc as `<agent-repo>/<agent-name>_memory/implemented.md`. 
This document is the handoff artifact to the refine phase. The Critic will read
both this document and the memory source code during refine; ensure all component
write/read methods are importable from the repo root.

It must be: 
- **Complete**: covers the mechanism of each memory function and the entire memory system. 
- **Actionable**: specific enough that the refine Critic can design tests and write
  a runner without re-exploring the agent from scratch. 

```markdown
# Memory Implementation: <agent-name>

## Memory Architecture
[High-level workflow of built memory system]

## Memory Implemented
[what is actually built (structure, files, key decisions, functions, integrations)]

## Memory Type Coverage
[For each memory type: how current system records it]

## Known Limitations
[Any limitations or known issues with the implementation]

## Memory Components
[For each memory component, an explicit contract block. The refine Critic
reads these to compose testable memory paths and write the trace runner.
Repeat the block below for every component.]

### Component: <name>
- `purpose`            (1 line; what this component stores/provides)
- `write_method`       (import path + call signature, e.g.
                        `from pkg.stores import DecisionsLog; DecisionsLog.append(ticker, record)`)
- `write_trigger`      (system_trigger | agent_tool_call | user_manual | none)
- `read_method`        (import path + call signature, e.g.
                        `DecisionsLog.read_last_n(ticker, n=3)`)
- `read_returns`       (return type description, e.g. "list of dicts with keys
                        trade_date, rating, one_line_thesis, key_risks")
- `eval_mode`          (qa | hit_rate)
- `persistence`        (file/db path, e.g. `~/.agent/memory/decisions/<TICKER>.jsonl`)
- `requires_flush_window` (true | false)
- `flush_window_method` (import path + call signature; required when
                        `requires_flush_window` is true, omit otherwise)
- `dependencies`       (other components this depends on, or "none")
- `notes`              (anything the Critic should know, e.g. "LLM-dependent
                        output", "top-k retrieval makes exact-match assertions
                        fragile", "read-only / fixture-only")

Components with `write_trigger: none` are fixture-only: the Critic may seed
them to provide context for other components but does not test their write path.
```

Update note.md with append to reflect latest understanding towards target agent and work done.

## Hard Constraints

- **Decoupling**: the memory module does not import agent internals
  (except LLM/embedding adapters). The agent imports the memory module,
  never the reverse.
- **Real LLM**: all tests involving LLM calls must use a real provider,
  not mocks.
- **Precomputed artifacts**: embeddings, indices, etc. must be persisted —
  never recompute what already exists on disk.
- **Environment bail-out**: if the agent fails to run after 3 attempts
  due to environment issues, stop and ask the user to fix it.

## Output

At the end of this command:
- Working memory system integrated into the agent.
- Functional tests passing.
- `<agent-repo>/<agent-name>_memory/implemented.md` exists and is complete,
  with per-component test contracts in the `Memory Components` section.
- `worktraces/refine_state.json` initialized with `step_status: "ready"`.
- `note.md` updated with implementation details.

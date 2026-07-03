---
name: auto-memory
description: End-to-end memory system creation — design, implement, and refine
argument-hint: agent-repo-path [user-requirements]
alwaysApply: true
---

# auto-memory

**Target agent repository**: $ARGUMENTS

## Goal

Orchestrate the full memory-building pipeline by running each phase in
sequence. This is a convenience wrapper — each sub-command can also be
run independently.

## Pipeline

### 1. Design

```
/auto-memory-design <agent-repo> [user-requirements]
```

Explore the agent and produce `memory_design.md`.

### 2. Implement

```
/auto-memory-implement <agent-repo>
```

Build the memory system from the design, write the refine adapter,
produce `implemented.md`, and initialize `refine_state.json`.

### 3. Refine

```
/auto-memory-refine <agent-repo>
```

Run the Critic-Improver loop until the Critic stops or max phases are
reached. The command handles the loop internally.

## Notes

- Each sub-command is designed to run in its own session to avoid context
  drift. If running end-to-end in a single session, be aware that context
  quality may degrade during later refine phases.
- Cross-session state is carried by `note.md`, `memory_design.md`, `implemented.md`, 
  and `refine_state.json`. No information needs to be passed manually between
  commands.

---
name: auto-memory-design
description: Explore an agent and design a tailored memory system
argument-hint: agent-repo-path [user-requirements]
alwaysApply: true
---

# auto-memory-design

**Target agent repository**: $ARGUMENTS

## note.md — Your Working Memory

**First thing you do**: create `<agent-repo>/<agent-name>_memory/worktraces/note.md`
(template at `@Auto_Memory/scaffolds/note-template.md`)

This file is your own persistent working memory across the entire pipeline.
Write into it anything you learn, decide, or need to revisit — user
requirements, design rationale, key discoveries, open questions, file paths.
You will be judged on what you build, not on what you write in note.md, so
optimize it for your own future use rather than presentation.

Update note.md as you go — before starting something, write your intent and
open questions; after finishing, write what actually happened, how it differed
from your plan, and what's still unresolved. The Critic reads note.md during
refine to design its tests; stale notes mean misaligned testing.

## Goal

Explore the target agent and produce a complete memory design document
(`memory_design.md`). This command works identically whether the agent has
existing memory or not — "no memory" is the extreme case where the gap is
everything.

## Tool

During your design, consult the reference system for proven patterns:
```
pip install -e Auto_Memory/reference/hub/    # install first (once)
ask_ref ls                                   # list all categories
ask_ref ls extraction                        # list one category
ask_ref grep "keyword"                       # search by keyword
ask_ref grep "keyword" -l rigorous           # filter by logic style
ask_ref grep "keyword" -c architecture       # filter by category
ask_ref cat <technique-id>                   # full technique card
```

Search by the **problem you are trying to solve** (e.g., "tool output",
"compaction", "eviction", "dedup"), not by weight class. The catalog is
small enough that keyword search returns all relevant techniques
regardless of weight.

## What to Do

### 1. Explore the Agent

Understand the target agent deeply before making any design decisions.

**Read the codebase:**
- README, entry point, config files, dependency manifest.
- Use subagent for broad exploration if the codebase is large.
- Identify agent's exisiting memory system, document it with detail with possible:
  components, memory flows, context assembly, etc.

**Run the agent (1–2 times on simple tasks):**
- Observe the full lifecycle: how a session starts, how decisions are made,
  how the session ends, what persists afterward.
- Observe the agent's output behavior, tone, and formatting.

**Retention window:**
If the agent has a retention window, explicitly document it.
- The sliding buffer of the last K raw turns
  (or tokens) that remain directly visible in the model input at
  inference time. Document:
  - The eviction mechanism (token budget, sliding window, compaction,
    session boundary) and its threshold.
  - What happens to content that leaves the window (archived,
    summarized, discarded).

### 2. Gap Analysis

If the agent already has some form of memory (explicit or implicit),
analyze the gap between what exists and what should exist.

For each gap, document:

- **What information** is being lost, mishandled, or missing?
- **When** does the loss happen? (context eviction, session boundary,
  tool output discard, etc.)
- **Impact**: what user-visible behavior results from this gap?
- **Root cause**: is this a timing issue (wrong write/recall point), a
  representation issue (wrong storage form), or a missing capability?

If the agent has no memory at all, the gap analysis is simple: everything
the user wants remembered is currently lost. Focus on characterizing
*what* information is worth remembering and *when* it becomes available.

### 3. Design the Memory System

Based on exploration and gap analysis, decide **what** the memory system
needs to remember and **how** it should behave. Do not decide implementation
details (file layout, code structure, integration method) — that belongs
to the implement phase.

**Architectural decisions:**

- **Weight class** — light (flat files, single database, ≤1 LLM call per
  operation), heavy (multi-backend, embeddings, multiple LLM calls), or
  medium? Match the agent's own architecture and latency tolerance.
- **Memory types** — from gap analysis, identify information that is expensive 
  to re-acquire, useful in future sessions, and reasonably stable.
  Do not assume memory is mainly user preference or profile. Consider but not limit to:
  1. **Reusable results / experience** — failure patterns, successful
     procedures, reusable tool outputs, environment-specific quirks.
  2. **User-scoped information** — stable preferences, constraints, or
     profile when they matter to the agent.
  3. **Task and context state** — plans, partial progress, unresolved items,
     and other context that must survive across sessions.
  4. **Session and trajectory history** — past conversations, working
     trajectories, interaction history that may need compression, indexing,
     or selective recall.

**Operational details (for each memory type):**

- **Write actor** — who causes memory writes, and at what granularity:
  - System-triggered(hooks, events, session end, compaction
    callback): the system processes content in bulk as it leaves the retention window.
  - Agent-triggered (agent calls memory tools during
    conversation): the agent decides per-turn whether to write,
    while content is still inside the retention window.
- **Write timing** — when does each kind of information become eligible?
- **Write method** — rigorous (rule-based extraction) or intelligent 
  (LLM-driven extraction)?
- **Storage form** — what representation is stored?
- **Update semantics** — new information: added, merged, overwritten,
  deduplicated, or deleted?
- **Recall timing** — at what stage of the agent loop is this recalled?
- **Recall method** — how is relevance detected, how is retrieved memory
  formatted for use?
- **Injection point** — where does recalled memory enter the agent's
  decision process?
- **Budget fit** — how does the design stay within the agent's latency
  and context budget?

### 4. Verify Gap Coverage

Before writing the design document, check that every gap from step 2 is
explicitly addressed by the design from step 3. For each gap, answer:

- **Which memory type / mechanism** addresses this gap?
- **Why is it sufficient?** Does the write timing capture the information
  before it is lost? Does the recall method surface it when needed?
- **What could still fall through?** Are there edge cases where this gap
  is only partially closed?

If a gap is not covered, go back to step 3 and revise the design. If a
gap is intentionally left unaddressed, state why (e.g., out of scope,
low impact, would require disproportionate complexity).

### 5. Produce the Design Document

Write the final design as `<agent-repo>/<agent-name>_memory/memory_design.md`.

This document is the handoff artifact to the implement phase. It must be:

- **Complete**: covers every decision from step 3, every gap from step 2,
  and the explicit mapping between them from step 4.
- **Actionable**: specific enough that the implementer can build from it
  without re-exploring the agent.

Suggested structure (adapt as needed):

```markdown
# Memory Design: <agent-name>

## Agent Summary
[Brief description of the agent, its architecture, key observations]

## Gap Analysis
[For each gap: what information is lost, when, impact, root cause]

## Design Decisions
### Weight Class
### Memory Types
[For each type: ALL operational details]

## Gap Coverage
[For each gap from the analysis: which design decision addresses it]

## Open Questions
[Anything unresolved that the implementer should decide]
```

Update note.md with a summary of the design decisions.

## Output

At the end of this command:
- `<agent-repo>/<agent-name>_memory/memory_design.md` exists and is complete.
- `<agent-repo>/<agent-name>_memory/worktraces/note.md` is up to date.

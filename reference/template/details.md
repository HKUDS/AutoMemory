# Details for <memory_system_name>

This file contains the deeper implementation-oriented explanation behind the skills extracted from this memory system.

Its purpose is to support:
- deeper borrowing of design ideas
- implementation reference
- comparison across memory systems

# What to include

Organize this file into clearly labeled sections as needed.

## 1. System architecture
- Main memory components
- Their responsibilities
- How they interact

## 2. Data model and schema
- Memory types
- Data structure for each type
- Important fields and their meaning
- Update semantics (append / merge / overwrite / reconcile)

## 3. Storage and indexing
- Storage backend choices
- Indexing strategy
- Retrieval units
- Any hybrid storage design

## 4. Retrieval logic
- Candidate generation
- Filtering
- Ranking / reranking
- Conflict handling
- Formatting before returning to the agent

## 5. Store and update logic
- Store triggers
- Extraction / transformation steps
- Deduplication
- Slot update vs new item creation
- Validation or normalization rules

## 6. Consolidation and lifecycle
- Consolidation policy
- TTL / compaction / eviction / archival
- Handling stale or conflicting memory

## 7. Important implementation details
- Key code snippets
- Important classes / functions
- Non-obvious engineering decisions
- Constraints or caveats

## 8. Transferability notes
- Which parts are reusable in other memory systems
- Which parts are tightly coupled to the original project
- What kinds of agents or workflows this design fits best

# What NOT to include

Do not dump the entire repo or copy large source files.
Only include details that are useful for understanding or reusing the design.

# Cross-reference

When a section supports a specific reusable skill, explicitly mention the corresponding `skill.md`.
When prompts are involved, point to the relevant file(s) in `prompts/`.
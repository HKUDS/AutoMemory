# Meta Memory Router Classification

Meta Memory Agent classifies content and routes to specialized memory managers via `trigger_memory_update`.

**Source:** `mirix/prompts/system/meta_memory_agent.txt`

## Decision Framework (evaluation order)

1. **Core Memory**: WHO the user is, HOW to communicate with them. User identity, preferences, personality traits, relationships.
2. **Episodic Memory**: WHAT happened WHEN. Events, activities, experiences with timestamps. Update for almost all user activities.
3. **Procedural Memory**: HOW TO do something. Step-by-step instructions, workflows, processes.
4. **Resource Memory**: WHAT files or documents. Shared files, documents, images, reference materials.
5. **Knowledge Vault**: Static REFERENCE DATA. Contact info, passwords, IDs, addresses, credentials.
6. **Semantic Memory**: NEW general knowledge or concepts. Definitions, people, places, organizations, abstract knowledge.

## Tool Call

```python
trigger_memory_update(memory_types=['core', 'episodic', 'resource', ...])
```

- Select from: `['core', 'episodic', 'semantic', 'resource', 'procedural', 'knowledge_vault']`
- When uncertain, include additional types rather than omit.
- After all updates, call `finish_memory_update`.

## Chaining

- Between `trigger_memory_update` calls, agent can reason (messages without tools = reasoning).
- Set `continue_chaining=True` until `finish_memory_update`.

## Reuse Advice

- Adapt classification rules for domain (e.g., coding: add "code_snippet" type).
- Use evaluation order to avoid ambiguity (e.g., Core vs Semantic: person info → Core if affects communication; Semantic if general knowledge about person).

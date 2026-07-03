# note.md — TradingAgents memory design

## User Requirements
- Target repo: `/data2/junzhou2/TradingAgents` (no other worktrees/forks).
- Entry point: `python run.py` → `TradingAgentsGraph.propagate(ticker, trade_date)`.
- The user runs the same agent on multiple tickers (NVDA, AAPL) on close-by dates.

## Agent Observations
- Each `propagate` goes: 4 analysts → Bull/Bear debate → Research Manager → Trader →
  Risk debate → Portfolio Manager. ~14 LLM calls + ~10 tool calls per run.
- Tool outputs are large (30-day OHLCV, indicators, news). They sit in `messages` until
  the analyst writes its report, then `Msg Clear <Analyst>` wipes the message list.
- Each analyst's final report is preserved in AgentState
  (`market_report`, `sentiment_report`, `news_report`, `fundamentals_report`).
- `run.py` never calls `reflect_and_remember()`.

## Discoveries
1. **BM25 memories were in-process only** before implementation — nothing persisted.
   Now backed by JSONL, auto-loaded on init, auto-flushed on every `add_situations`.
2. **No persistent ticker knowledge** existed — each propagate re-derived everything.
   Now handled by per-ticker knowledge cards (Type K).
3. **Stock CSV is cached** on disk; analyst LLM reports and news were not.
   Now analyst reports are cached by Type C (AnalystCache).
4. Stale `~/.tradingagents/memory/` artifacts from a prior Auto_Memory attempt existed.
   Implementation reuses the same paths, so existing data (NVDA card, NVDA recommendations)
   is picked up.

## Retention Window Mechanics
- The agent's "retention window" is `state["messages"]` inside LangGraph.
- Per analyst node, the window grows with tool outputs until the analyst's final response,
  then `Msg Clear <Analyst>` removes all messages and appends one `HumanMessage("Continue")`.
- Memory is injected as prompt text (not messages), so it survives Msg Clear evictions.
- Across runs, everything in LangGraph state is discarded; only disk-persisted memory survives.

## Design Decisions
- Weight class: **light → medium**. No embeddings; BM25 for reflections, file-backed stores
  for everything else. No new agents, no graph DB.
- Six memory types: R (reflections), K (knowledge card), D (decisions log),
  U (user context), M (macro notes), C (analyst cache).
- All writes are system-triggered (post-propagate or pre-graph). No agent tool calls for memory.
- Per-component contracts are documented in `implemented.md § Memory Components`.

## Known Weaknesses
- Self-reflection without realized returns can only catch consistency mistakes, not outcome mistakes.
- Analyst cache has no automatic invalidation when model changes (exact key match only).
- Macro notes write is not cross-process safe (single-process flag only).
- Knowledge card cold-start: first run for a new ticker has no card.
- Gap G7 (tool-output insights) not addressed in v1.

## Runtime Configs
- Conda env: `tradingagents`
- LLM provider: OpenRouter (Qwen for tests)
- API keys in `/data2/junzhou2/TradingAgents/.env`
- Test environment: conda environment `tradingagents`
- Test command: `cd /data2/junzhou2/TradingAgents && conda run -n tradingagents python -m pytest tradingagents-memory/tests/test_memory.py -v`
- 28 tests: all pass.

## Refine Summaries
<!-- Appended at the end of refine session. Read-only during refine. -->

### Session — 2026-04-23 (Phases 0–5, stopped at Phase 6 by user)

**Phases completed**: 5 Critic phases (0–5) + 5 Improver phases (1–5).
**Traces built**: 17 traces across 4 memory paths + 1 e2e.
**Final graduated paths**: knowledge_reflection, analyst_cache, decisions_log, macro.
**Final scores (Phase 5)**: all 17 traces at sc=1.0, qs=1.0 (or hit_rate=1.0). e2e=1.0.

**Bugs found and fixed (by phase):**

| Phase | Severity | Component | Bug | Fix |
|---|---|---|---|---|
| 0→1 | High | MacroRefresher | Silent week drop when LLM uses numbered/prose format instead of `- ` bullets | `_extract_macro_bullets()` handles all formats; RuntimeWarning on empty |
| 0→1 | Medium | BM25Reflections | SITUATION texts lacked domain keywords (sector, metric, risk type), causing BM25 recall mismatches | Prompt updated to require domain-grounded SITUATION sentences |
| 0→1 | Low | KnowledgeCardWriter | LLM added preamble headers and `#### ## Identity` nesting despite "no preamble" instruction | Stronger prompt + `_clean_knowledge_card()` post-processor |
| 1→2 | Low | KnowledgeCardWriter | Duplicate `Last updated:` line on every merge (LLM copied existing timestamp, caller appended another) | `_LAST_UPDATED_RE` filter in `_clean_knowledge_card()` strips LLM-copied timestamps before appending |
| 2→3 | Medium | DecisionsLog | `read_last_n` returned records by JSONL insertion order, not `trade_date` | Parse all lines, sort by `trade_date`, slice last N |
| 3→4 | Medium | DecisionsLog | `read_last_n(n=0)` returned all records (Python `-0` = `0` slice gotcha) | `if n <= 0: return []` guard |
| 3→4 | Medium | MacroNotes | `read_recent(n_weeks=0)` returned all sections (same Python `-0` gotcha) | `if n_weeks <= 0: return ""` guard |
| 3→4 | High | DecisionsLog | Ring-buffer cap evicted by insertion order, inconsistent with date-order `read_last_n` | Cap enforcement now sorts by `trade_date` before slicing |
| 4→5 | Medium | MacroNotes | `write_section` appended sections without sorting; OOO backfill writes caused `read_recent` to return wrong weeks | Sort `new_sections` by ISO week in `write_section()`; also sort in `read_recent()` for robustness |

# Memory Implementation: TradingAgents

## Memory Architecture

```
propagate(ticker, trade_date)
  │
  ├─ PRE-GRAPH ──────────────────────────────────────────────────────────
  │   MacroRefresher.refresh_if_stale(trade_date)          [once/process]
  │     └─ if stale: call get_global_news → LLM → write macro_notes.md
  │
  ├─ GRAPH EXECUTION ────────────────────────────────────────────────────
  │   Market Analyst node
  │     ├─ hit  → AnalystCache.check("market") → return cached report
  │     └─ miss → LLM + tools → write report → AnalystCache.write("market")
  │
  │   Social Analyst node    (same cache pattern)
  │   News Analyst node      (same cache pattern + MacroNotes.read_recent injected)
  │   Fundamentals Analyst   (same cache pattern)
  │
  │   Bull / Bear Researcher
  │     └─ FinancialSituationMemory.get_memories(curr_situation) → BM25 recall
  │
  │   Research Manager
  │     ├─ BM25 recall (invest_judge_memory)
  │     └─ DecisionsLog.read_last_n(ticker, 3) → "Prior decisions" block
  │
  │   Trader
  │     ├─ BM25 recall (trader_memory)
  │     └─ UserContext.read(ticker) → portfolio + profile block
  │
  │   Risk debators (Aggressive/Conservative/Neutral)
  │     └─ no memory read (debators have no memory bucket)
  │
  │   Portfolio Manager
  │     ├─ BM25 recall (portfolio_manager_memory)
  │     ├─ DecisionsLog.read_last_n(ticker, 3) → "Prior decisions" block
  │     ├─ UserContext.read(ticker) → portfolio + profile block
  │     └─ MacroNotes.read_recent(2) → macro context block
  │
  ├─ POST-GRAPH (in propagate(), after graph.invoke()) ──────────────────
  │   DecisionsLog.append(ticker, {date, rating, thesis, risks})
  │   KnowledgeCardWriter.merge(state, ticker, date)      [1 LLM call]
  │   SelfReflector.run(state, memories)                  [1 LLM call]
  │     └─ appends (situation, lesson) pairs to all 5 BM25 buckets
  │
  └─ AFTER propagate() (user-triggered) ─────────────────────────────────
      reflect_and_remember(returns_losses)                [existing path]
        └─ full outcome-based reflection → appends to BM25 buckets
```

**Key property**: the retention window (`state["messages"]`) is flushed per analyst
node by the existing `Msg Clear <Analyst>` LangGraph nodes. Memory is injected as
prompt text (not messages), so it survives across Msg Clear evictions automatically.

---

## Memory Implemented

### New files

| File | Purpose |
|------|---------|
| `tradingagents/agents/utils/memory_stores.py` | Disk I/O primitives for Types C/D/K/M/U. No LLM, no agent imports. |
| `tradingagents/graph/self_reflection.py` | LLM-based post-propagate writes: `SelfReflector`, `KnowledgeCardWriter`, `MacroRefresher`. |
| `tradingagents-memory/tests/test_memory.py` | 28 per-component disk tests + 1 optional e2e `propagate()` test (`@pytest.mark.llm` / `slow` / `network`, skips if no `OPENROUTER_API_KEY`). |

### Modified files

| File | Change |
|------|--------|
| `tradingagents/agents/utils/memory.py` | Added JSONL persistence to `FinancialSituationMemory`: loads from `~/.tradingagents/memory/agent/reflections/<name>.jsonl` on init; flushes on every `add_situations`. Cap=500. |
| `tradingagents/agents/utils/agent_utils.py` | `build_instrument_context()` now appends the ticker knowledge card (Type K) if present. |
| `tradingagents/agents/analysts/market_analyst.py` | Cache check at start (Type C); cache write after complete report. |
| `tradingagents/agents/analysts/social_media_analyst.py` | Same Type C pattern. |
| `tradingagents/agents/analysts/news_analyst.py` | Same Type C pattern + injects `MacroNotes.read_recent()` into system prompt (Type M). |
| `tradingagents/agents/analysts/fundamentals_analyst.py` | Same Type C pattern. |
| `tradingagents/agents/managers/research_manager.py` | Injects `DecisionsLog.read_last_n(3)` as "Prior decisions" block (Type D). |
| `tradingagents/agents/managers/portfolio_manager.py` | Injects Type D (decisions), Type U (user context), Type M (macro notes). |
| `tradingagents/agents/trader/trader.py` | Injects Type U (user portfolio + profile). |
| `tradingagents/graph/trading_graph.py` | Pre-graph: `MacroRefresher.refresh_if_stale`. Post-graph: `DecisionsLog.append`, `KnowledgeCardWriter.merge`, `SelfReflector.run`. |

### Storage layout (all under `~/.tradingagents/memory/`)

```
memory/
├── agent/
│   ├── reflections/
│   │   ├── bull_memory.jsonl            # Type R: BM25 bucket for Bull researcher
│   │   ├── bear_memory.jsonl            # Type R: BM25 bucket for Bear researcher
│   │   ├── trader_memory.jsonl          # Type R: BM25 bucket for Trader
│   │   ├── invest_judge_memory.jsonl    # Type R: BM25 bucket for Research Manager
│   │   └── portfolio_manager_memory.jsonl  # Type R: BM25 bucket for PM
│   ├── ticker_knowledge/
│   │   └── <TICKER>.md                 # Type K: per-ticker knowledge card
│   ├── recommendations/
│   │   └── <TICKER>.jsonl              # Type D: per-ticker decisions log
│   ├── analyst_cache/
│   │   └── <TICKER>/<DATE>/
│   │       ├── market.json             # Type C: cached market analyst report
│   │       ├── social.json             # Type C: cached social analyst report
│   │       ├── news.json               # Type C: cached news analyst report
│   │       └── fundamentals.json       # Type C: cached fundamentals report
│   └── macro_notes.md                 # Type M: rolling weekly macro notes
└── user/
    ├── portfolio.json                  # Type U: user holdings (user-edited)
    └── profile.md                      # Type U: user constraints (user-edited)
```

---

## Memory Type Coverage

| Type | Name | How written | How read | LLM? |
|------|------|-------------|----------|-------|
| R | BM25 Reflections | Post-propagate `SelfReflector.run()` + existing `reflect_and_remember()` | Top of Bull/Bear/RM/Trader/PM nodes via `FinancialSituationMemory.get_memories()` | Write: yes (self-reflection prompt). Recall: no (BM25). |
| K | Ticker Knowledge Card | Post-propagate `KnowledgeCardWriter.merge()` | Via `build_instrument_context()` → injected in every node's system prompt | Write: yes (merge prompt). Recall: no (file read). |
| D | Decisions Log | Post-propagate `DecisionsLog.append()` via regex extraction | Research Manager and Portfolio Manager nodes | Write: no (regex). Recall: no (file read). |
| U | User Context | Never (user edits manually) | Trader and Portfolio Manager nodes | No. |
| M | Macro Notes | Pre-graph `MacroRefresher.refresh_if_stale()` (once/process) | News Analyst and Portfolio Manager nodes | Write: yes (summarization). Recall: no (file read). |
| C | Analyst Cache | End of each analyst node (on complete report) | Start of each analyst node (cache hit skips entire LLM loop) | No. |

---

## Known Limitations

1. **Self-reflection without realized returns** — `SelfReflector` can only flag consistency issues (ignored Bear risks, contradictions, logical gaps), not outcome mistakes. Quality improves once users supply `returns_losses` to `reflect_and_remember()`.
2. **Type C cache invalidation** — Cache is keyed by `(ticker, trade_date, analyst_type)`. There is no automatic invalidation when the model changes. Delete the date folder to force a fresh run.
3. **Macro notes write concurrency** — If two `propagate()` calls run in parallel processes for different tickers on the same day, both may attempt to write `macro_notes.md`. A simple process-level flag (`_macro_refresh_done`) prevents double-write within a single process, but is not cross-process safe.
4. **Knowledge card cold-start** — First run for a new ticker has no card, so `build_instrument_context()` returns the base string only. The card is written after the first propagate completes.
5. **Gap G7 (tool-output insights) not addressed** — Hooking the `ToolNode` is out of scope for v1. The per-run JSON log already captures this data for future mining.

---

## Memory Components

### Component: DecisionsLog

- `purpose`: Append-only per-ticker JSONL log of trading decisions (date, rating, thesis, risks).
- `write_method`: `from tradingagents.agents.utils.memory_stores import DecisionsLog; DecisionsLog.append(ticker, record)` where `record = {"trade_date": str, "ticker": str, "rating": str, "one_line_thesis": str, "key_risks": [str]}`
- `write_trigger`: system_trigger
- `read_method`: `DecisionsLog.read_last_n(ticker, n=3)`
- `read_returns`: list of dicts with keys `trade_date`, `ticker`, `rating`, `one_line_thesis`, `key_risks`
- `eval_mode`: qa
- `persistence`: `~/.tradingagents/memory/agent/recommendations/<TICKER>.jsonl`
- `requires_flush_window`: false
- `dependencies`: none
- `notes`: Write deduplicates by `trade_date` (same date overwrites prior entry). Ring buffer capped at 500. No LLM involved in write or read.

### Component: KnowledgeCard

- `purpose`: Per-ticker markdown knowledge card summarizing stable fundamentals, recent technicals, and PM rating history.
- `write_method`: `from tradingagents.graph.self_reflection import KnowledgeCardWriter; writer = KnowledgeCardWriter(llm); writer.merge(state, ticker, trade_date)` — this internally calls `from tradingagents.agents.utils.memory_stores import KnowledgeCard; KnowledgeCard.write(ticker, content)`
- `write_trigger`: system_trigger
- `read_method`: `from tradingagents.agents.utils.memory_stores import KnowledgeCard; KnowledgeCard.read(ticker)`
- `read_returns`: string (full markdown card body), or empty string if no card exists
- `eval_mode`: qa
- `persistence`: `~/.tradingagents/memory/agent/ticker_knowledge/<TICKER>.md`
- `requires_flush_window`: false
- `dependencies`: none
- `notes`: LLM-dependent output — the merge prompt produces non-deterministic card content. `KnowledgeCard.write()` is a full overwrite (not append). Cold-start returns empty string.

### Component: BM25Reflections

- `purpose`: Per-agent BM25-indexed situation/lesson pairs from post-propagate self-reflection. Five buckets: bull_memory, bear_memory, trader_memory, invest_judge_memory, portfolio_manager_memory.
- `write_method`: `from tradingagents.graph.self_reflection import SelfReflector; reflector = SelfReflector(llm); reflector.run(state, memories)` where `memories` is a dict of `{name: FinancialSituationMemory}`. This internally calls `memory.add_situations([(situation, lesson), ...])`.
- `write_trigger`: system_trigger
- `read_method`: `from tradingagents.agents.utils.memory import FinancialSituationMemory; mem = FinancialSituationMemory(name); mem.get_memories(current_situation, n_matches=2)`
- `read_returns`: list of dicts with keys `matched_situation`, `recommendation`, `similarity_score`
- `eval_mode`: qa
- `persistence`: `~/.tradingagents/memory/agent/reflections/<name>.jsonl` (5 files, one per bucket)
- `requires_flush_window`: false
- `dependencies`: none
- `notes`: LLM-dependent output — `SelfReflector` may emit 0-3 pairs depending on LLM judgment. BM25 retrieval is lexical (no embeddings), so exact-match assertions are fragile; use semantic or substring matchers. Ring buffer capped at 500 per bucket. Read requires `FinancialSituationMemory(name)` constructor which rebuilds BM25 index from JSONL on init.

### Component: MacroNotes

- `purpose`: Rolling weekly macro briefing summarizing global news themes relevant to equity trading.
- `write_method`: `from tradingagents.graph.self_reflection import MacroRefresher; refresher = MacroRefresher(llm, get_global_news_fn); refresher.refresh_if_stale(trade_date)` — this internally calls `MacroNotes.write_section(iso_week, bullets, trade_date)`
- `write_trigger`: system_trigger
- `read_method`: `from tradingagents.agents.utils.memory_stores import MacroNotes; MacroNotes.read_recent(n_weeks=2)`
- `read_returns`: string (markdown with H2 weekly sections), or empty string if no notes exist
- `eval_mode`: qa
- `persistence`: `~/.tradingagents/memory/agent/macro_notes.md`
- `requires_flush_window`: false
- `dependencies`: none
- `notes`: LLM-dependent output. Write is idempotent per iso_week (same week overwrites). Once-per-process guard (`should_refresh_macro` + `mark_macro_refresh_done`). Rolling cap of 8 weekly sections. Requires external `get_global_news_fn` callable.

### Component: AnalystCache

- `purpose`: Exact-key cache for analyst reports, keyed by (ticker, trade_date, analyst_type). Cache hit skips the entire analyst LLM loop.
- `write_method`: `from tradingagents.agents.utils.memory_stores import AnalystCache; AnalystCache.write(ticker, trade_date, analyst_type, report)`
- `write_trigger`: system_trigger
- `read_method`: `AnalystCache.check(ticker, trade_date, analyst_type)`
- `read_returns`: string (cached report body) on hit, or `None` on miss
- `eval_mode`: hit_rate
- `persistence`: `~/.tradingagents/memory/agent/analyst_cache/<TICKER>/<DATE>/<type>.json`
- `requires_flush_window`: false
- `dependencies`: none
- `notes`: No LLM in write or read. Write overwrites any prior entry for the same key. No automatic invalidation — exact (ticker, date, type) match only. Four analyst types: `market`, `social`, `news`, `fundamentals`.

### Component: UserContext

- `purpose`: Read-only user portfolio holdings and risk profile, injected into Trader and Portfolio Manager prompts.
- `write_method`: none (user edits files manually)
- `write_trigger`: none
- `read_method`: `from tradingagents.agents.utils.memory_stores import UserContext; UserContext.read(ticker)`
- `read_returns`: string (formatted portfolio + profile block), or empty string if files absent
- `eval_mode`: qa
- `persistence`: `~/.tradingagents/memory/user/portfolio.json` and `~/.tradingagents/memory/user/profile.md`
- `requires_flush_window`: false
- `dependencies`: none
- `notes`: Fixture-only / read-only. The Critic may seed `portfolio.json` and `profile.md` to provide context for other components but does not test the write path. `read(ticker)` returns "No current position in {ticker}." when the ticker is not in portfolio.

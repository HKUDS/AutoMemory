# Memory Design: TradingAgents

Target agent: `/data2/junzhou2/TradingAgents`
Entry point: `python run.py` → `TradingAgentsGraph.propagate(ticker, trade_date)`
Design date: 2026-04-20

---

## 1. Agent Summary

TradingAgents is a LangGraph multi-agent pipeline that produces a trading
recommendation for one `(ticker, trade_date)` per `propagate()` call.

Pipeline (≈14 LLM calls + ≈10 tool calls per run):

```
START
  → Market Analyst    ⇄ tools_market    → Msg Clear Market
  → Social Analyst    ⇄ tools_social    → Msg Clear Social
  → News Analyst      ⇄ tools_news      → Msg Clear News
  → Fundamentals      ⇄ tools_fund.     → Msg Clear Fundamentals
  → Bull Researcher ⇄ Bear Researcher   (max_debate_rounds × 2 turns)
  → Research Manager
  → Trader
  → Aggressive ⇄ Conservative ⇄ Neutral (max_risk_discuss_rounds × 3 turns)
  → Portfolio Manager
END
```

Key state (LangGraph `AgentState`, all in-RAM, discarded at process exit):
- `messages` — the LangChain message list, the *retention window*.
- `market_report`, `sentiment_report`, `news_report`, `fundamentals_report`
  — the four analyst outputs, written into structured fields and survive
  the per-analyst Msg Clear evictions.
- `investment_debate_state`, `risk_debate_state` — debate transcripts.
- `investment_plan`, `trader_investment_plan`, `final_trade_decision`.

Existing "memory" — `FinancialSituationMemory` (BM25):
- Five named instances: `bull_memory`, `bear_memory`, `trader_memory`,
  `invest_judge_memory`, `portfolio_manager_memory`.
- All five are consulted at the top of their respective node
  (`memory.get_memories(curr_situation, n_matches=2)`) and the result is
  templated into the prompt as `past_memory_str`.
- Storage is an in-RAM Python list. **Nothing is written to disk.**
- `add_situations(...)` is only invoked by `Reflector` methods, which are
  only invoked by `TradingAgentsGraph.reflect_and_remember(returns_losses)`,
  which is **never called from `run.py`**. So in current usage the BM25
  index is always empty and `past_memory_str` is always `""`.

Already-persistent state (independent of the memory question):
- Stock CSV cache: `~/.tradingagents/cache/<TICKER>-YFin-data-*.csv`.
- Per-run full state log:
  `~/.tradingagents/logs/<TICKER>/TradingAgentsStrategy_logs/full_states_log_<date>.json`.
- Stale directory from a prior Auto_Memory attempt:
  `~/.tradingagents/memory/{agent,user,feedback}/...`. Code does NOT read
  it, but its files are intact and we will reuse the schema.

### Retention window mechanics
- The retention window is `state["messages"]`. It is *not* token-bounded;
  eviction is event-driven.
- Inside an analyst node, the message list grows turn by turn (very large
  tool outputs included) until the model emits a non-tool-call response.
- `should_continue_<analyst>` then routes to `Msg Clear <Analyst>`, which
  emits `RemoveMessage(id=m.id)` for every message and appends a single
  `HumanMessage("Continue")`. Concrete trigger: `last_message.tool_calls`
  is empty/falsy.
- After eviction the analyst's output survives only as the structured
  `*_report` field on `AgentState`.
- Across `propagate()` calls, the entire `AgentState` is discarded. The
  only cross-session survivors are the cache CSV and the `_log_state` JSON.

---

## 2. Gap Analysis

| ID | Gap | When information is lost | User-visible impact | Root cause |
|----|-----|--------------------------|---------------------|------------|
| **G1** | Reflection lessons never persist across runs | Process exit | Every prompt that says "Reflections from similar situations and lessons learned" injects `""`; the wording promises learning that the agent can never deliver. | `FinancialSituationMemory` is in-RAM; no read/write to disk; `reflect_and_remember` is never called from the entry point. |
| **G2** | Same `(ticker, trade_date)` re-runs from scratch | Process exit | Same propagate ⇒ ~14 LLM calls + ~10 tool calls duplicated. The user's terminal trace shows literally re-running the same NVDA & AAPL analyses. | No cache between an analyst node and its own outputs. |
| **G3** | Stable ticker facts (sector, business model, last fundamentals snapshot, last rating) re-derived every run | Process exit | Fundamentals analyst re-explains the same balance sheet next day; market analyst re-discovers the same golden cross every session. | No durable per-ticker knowledge card. |
| **G4** | Macro / global news re-fetched and re-summarized once per ticker per day | Within a single multi-ticker workflow and across days | News and Social analysts produce nearly identical macro paragraphs for AAPL and NVDA on the same day; the Portfolio Manager has no shared macro view. | No daily macro note shared across tickers. |
| **G5** | Portfolio Manager rates without knowledge of existing positions, cash, or user risk profile | n/a — never had it | A "Buy" rating may contradict the user actually being already 30% long; no constraint enforcement (e.g. "no leverage", "no crypto"). | No user/portfolio injection point. |
| **G6** | Decision drift not detected (e.g. yesterday HOLD → today BUY with no new evidence) | Process exit | PM and RM cannot reference yesterday's thesis; thesis quality fluctuates day-to-day for narrative reasons. | No recent-decisions log read at PM/RM time. |
| **G7** | Tool-output insights and per-tool quirks lost after analyst writes its report | Per-analyst Msg Clear | Cannot learn things like "get_news returned empty for week X for this ticker" or "get_indicators is unreliable on weekends". | No tool-observation capture. |
| **G8** | Stale `~/.tradingagents/memory/` from a previous Auto_Memory attempt is unreferenced | n/a | Confusing for maintainers; risks divergence if both old and new files coexist. | Code regression — schema was created but integration was lost. |

---

## 3. Design Decisions

### 3.1 Weight Class

**Light → Medium.** Rationale:
- The agent already pays for ~14 LLM calls per propagate. Any heavyweight
  memory architecture (per-memory-type agent, embedding store, graph DB)
  would add a comparable amount of latency for diminishing return.
- The two highest-impact wins (G1: persistence of existing BM25 lessons;
  G2/G3: deterministic per-ticker reuse) are achievable without ML
  infrastructure — they need disk + a single LLM merge call per session.
- BM25 is already part of the agent (`rank_bm25`). Reuse it. Do not add
  embeddings for v1.
- Add at most **one new LLM call per propagate** (the per-ticker
  knowledge-card merge), and **zero** new LLM calls per recall.

### 3.2 Memory Types

Six types, lettered for cross-reference with the gap table.

#### Type R — Reflection lessons (persistent BM25, 5 buckets)

| Field | Value |
|---|---|
| **Addresses** | G1 |
| **Owner** | Agent (system) |
| **Storage form** | One JSONL file per bucket: `{situation, recommendation, ts}` per line. BM25 index is rebuilt on load. |
| **Storage path** | `~/.tradingagents/memory/agent/reflections/{bull,bear,trader,invest_judge,portfolio_manager}.jsonl` (re-uses the existing-but-unused dir) |
| **Write actor** | System-triggered. |
| **Write timing** | (a) End of every `propagate()` — *self-reflection*: a degraded reflection that flags consistency problems (drift vs prior thesis, contradictions between analyst reports, missed risks named by Bear/Conservative), without realized returns. (b) Whenever `reflect_and_remember(returns_losses)` is later called — full outcome-based reflection (existing path). |
| **Write method** | Intelligent: existing `Reflector` LLM call, identical schema. |
| **Update semantics** | Append-only. Per bucket cap at **N=500**; on overflow, drop oldest line. (Implements the existing `add_situations` API plus a sidecar disk write.) |
| **Recall timing** | Top of each consumer node (Bull, Bear, Research Manager, Trader, Portfolio Manager) — exactly today's call sites. |
| **Recall method** | Intelligent retrieval substrate (BM25), already coded. `n_matches=2` per call. |
| **Recall format** | `past_memory_str` template, unchanged. |
| **Injection point** | Existing prompt slot in each node. **No prompt changes needed.** |
| **Budget fit** | Adds 0 LLM calls at recall (BM25 is local). Adds 1 LLM call at end of `propagate()` for self-reflection. Disk I/O is microseconds. |

#### Type K — Ticker knowledge card (per-ticker, always-in-context)

| Field | Value |
|---|---|
| **Addresses** | G3, G6 (partly) |
| **Owner** | Agent (system) |
| **Storage form** | Markdown file per ticker. Sections: `## Identity` (one paragraph: sector/business), `## Stable Fundamentals` (last snapshot table), `## Recent Technical Posture` (≤10 bullets, last update date), `## Recent PM Ratings` (≤5 lines, date · rating · one-line thesis). |
| **Storage path** | `~/.tradingagents/memory/agent/ticker_knowledge/<TICKER>.md` (re-uses the existing dir; the existing NVDA.md remains valid) |
| **Write actor** | System-triggered. |
| **Write timing** | End of `propagate()`, after `_log_state()`. |
| **Write method** | Intelligent — single LLM call: "merge this propagate's four reports + final rating into the existing card; preserve identity, refresh fundamentals only if newer, append latest technical bullets dropping ones >30 days old, prepend latest rating to the ratings list (cap 5)." Uses `quick_thinking_llm`. |
| **Update semantics** | Merge / dedupe by section. The card is bounded (~1.5–2 KB). Older versions are not retained (write-over). |
| **Recall timing** | At the start of every node that calls `build_instrument_context(ticker)` — analysts, trader, RM, PM. |
| **Recall method** | Rigorous: file read by ticker. |
| **Recall format** | Inlined into the existing `instrument_context` string. |
| **Injection point** | Modify `build_instrument_context()` in `agents/utils/agent_utils.py` to append the card body if a file is found. **One-line change at every call site.** |
| **Budget fit** | +1 LLM call per propagate (merge). Recall is a file read of <2 KB injected once per node. Net effect: usually a *reduction* in tokens because analysts can rely on the card instead of re-deriving facts. |

#### Type D — Decisions log (per-ticker, append-only)

| Field | Value |
|---|---|
| **Addresses** | G6 |
| **Owner** | Agent (system) |
| **Storage form** | One JSONL per ticker: `{trade_date, ticker, rating, one_line_thesis, key_risks: [str]}`. |
| **Storage path** | `~/.tradingagents/memory/agent/recommendations/<TICKER>.jsonl` (re-uses existing dir; the existing NVDA.jsonl is compatible). |
| **Write actor** | System-triggered. |
| **Write timing** | End of `propagate()`, derived from `final_trade_decision`. |
| **Write method** | Rigorous: regex / signal_processor extracts the rating; `quick_thinking_llm` extracts the one-line thesis and 2-3 key risks if structured parsing fails. |
| **Update semantics** | Append-only. Same `(trade_date, ticker)` overwrites the prior entry of the same day (rare). |
| **Recall timing** | (a) Research Manager node, (b) Portfolio Manager node. |
| **Recall method** | Rigorous: read last 3 records from `<TICKER>.jsonl`. |
| **Recall format** | Inserted into the prompt as a `## Prior decisions on this ticker` block: `{date}: {rating} — {thesis} (risks: …)`. |
| **Injection point** | Modify `create_research_manager` and `create_portfolio_manager` prompts (additive — append a single labeled block before the existing memory block). |
| **Budget fit** | Recall is a tail-read on a small JSONL (~kB). Write is one optional LLM call (skipped if regex parse succeeds — typical case). |

#### Type U — User profile + portfolio (always-in-context, read-only)

| Field | Value |
|---|---|
| **Addresses** | G5 |
| **Owner** | User (read-only by the agent) |
| **Storage form** | `portfolio.json` `{cash_balance: float, positions: [{ticker, qty, avg_cost, opened_on}]}`; `profile.md` (free text: risk tolerance, horizon, hard constraints). |
| **Storage path** | `~/.tradingagents/memory/user/{portfolio.json,profile.md}` (re-uses existing dir; existing files preserved). |
| **Write actor** | None (agent never writes). The user edits these files manually. |
| **Write timing** | n/a |
| **Write method** | n/a |
| **Update semantics** | n/a |
| **Recall timing** | At Trader node and Portfolio Manager node only. |
| **Recall method** | Rigorous: file read; portfolio filtered to current ticker’s position (if any) plus aggregate cash %. |
| **Recall format** | Templated block: `## Your portfolio context\nCash: $X (Y% of NAV)\nCurrent position in {ticker}: Z shares @ avg $W (P/L=…)\n## Profile\n{profile.md verbatim}`. |
| **Injection point** | Add to `create_trader` and `create_portfolio_manager` prompts (additive). |
| **Budget fit** | 2 file reads, no LLM cost. ~200 tokens added to two prompts. |

#### Type M — Macro daily notes (cross-ticker, always-in-context)

| Field | Value |
|---|---|
| **Addresses** | G4 |
| **Owner** | Agent (system) |
| **Storage form** | One markdown file with H2 sections per ISO week: `## 2026-W16` followed by ≤10 bullets covering global themes, mentioned tickers, source domains. |
| **Storage path** | `~/.tradingagents/memory/agent/macro_notes.md` (re-uses existing file). |
| **Write actor** | System-triggered. |
| **Write timing** | Inside `propagate()` *before* the News Analyst runs, **only if** the file's most recent entry is older than `trade_date - 1d` and we have not already refreshed in this process. |
| **Write method** | Intelligent: one `quick_thinking_llm` call that ingests `get_global_news(curr_date=trade_date, look_back_days=7)` and rewrites the latest week's H2 section. Bounded to ≤10 bullets ⇒ ≤500 tokens. |
| **Update semantics** | Section-keyed by ISO week: rewrite latest week, preserve older weeks. Cap file at 8 weeks; drop oldest H2 section beyond cap. |
| **Recall timing** | News Analyst and Portfolio Manager. |
| **Recall method** | Rigorous: read latest 2 H2 sections (~current week + prior week). |
| **Recall format** | Plain markdown block injected as `## Recent macro context (last 2 weeks)`. |
| **Injection point** | News Analyst system prompt; Portfolio Manager prompt. |
| **Budget fit** | At most 1 macro-refresh LLM call per process (deduplicated across multiple `propagate()` calls in the same script). Net effect: News Analyst usually does NOT need to call `get_global_news` again on the same day. |

#### Type C — Analyst report cache (per-(ticker,date,analyst), bypass node)

| Field | Value |
|---|---|
| **Addresses** | G2 |
| **Owner** | Agent (system) |
| **Storage form** | `{report: str, written_at: iso8601, model: str}` JSON files. |
| **Storage path** | `~/.tradingagents/memory/agent/analyst_cache/<TICKER>/<TRADE_DATE>/{market,social,news,fundamentals}.json`. |
| **Write actor** | System-triggered. |
| **Write timing** | End of each analyst node (when `result.tool_calls` is empty and `report` is non-empty). |
| **Write method** | Rigorous: dump the report verbatim. |
| **Update semantics** | Overwrite per `(ticker, date, analyst)` key. |
| **Recall timing** | At the *start* of each analyst node, **before** the LLM is invoked. |
| **Recall method** | Rigorous: exact-key file lookup. Hit ⇒ skip the LLM + tool loop entirely; return `{messages: [], <analyst>_report: cached}` so downstream nodes see a populated report and the Msg Clear becomes a no-op. |
| **Recall format** | Cached report becomes the analyst's `*_report` field unchanged. |
| **Injection point** | Wrap the four `create_*_analyst` factories (or, cleaner, a small `cache_or_run` decorator applied in `setup.py`). |
| **Budget fit** | On a cache hit: -1 analyst LLM call AND -tool calls (often 5–9). Same `(ticker, date)` re-run cost drops from ~14 LLM calls to ~6. |
| **Safety** | Exact-match key only (no fuzzy date reuse). Cache is invalidated by deleting the date folder. |

### 3.3 Operational Summary Table

| Type | Weight | Logic | Recall strategy | Write trigger | New LLM calls | New tool calls |
|------|--------|-------|-----------------|---------------|----------------|----------------|
| R    | light  | intelligent (BM25 + Reflector) | on-pattern (BM25) | session end, optional outcome-based | +1 self-reflect / propagate | 0 |
| K    | medium | intelligent (LLM merge) | always-in-context (per ticker) | session end | +1 / propagate | 0 |
| D    | light  | rigorous (regex) → optional intelligent | on-demand at RM, PM | session end | 0–1 / propagate | 0 |
| U    | light  | rigorous (file read) | always-in-context (Trader, PM) | n/a (user-edited) | 0 | 0 |
| M    | medium | intelligent (LLM rewrite) | always-in-context (News, PM) | once per process if stale | +1 / process | -k (saves repeated `get_global_news` per ticker) |
| C    | light  | rigorous (file lookup) | bypass-of-node | per analyst node end | -k (skips analyst LLM on hit) | -k (skips tool loop on hit) |

Net per-propagate cost vs today (cold cache, single ticker):
- LLM: +3 (R, K, M).
- Tool calls: 0.

Net per-propagate cost vs today (warm cache, same ticker same date re-run):
- LLM: -8 to -10 (4 analysts skipped + R/K/M write skipped on no-op detection).
- Tool calls: -5 to -10.

---

## 4. Gap Coverage

| Gap | Addressed by | Why sufficient | Residual risk |
|-----|--------------|----------------|---------------|
| **G1** Reflections never persist | R (persistent JSONL + auto-write at session end) | The exact existing call sites now read non-empty BM25 indices on second and later runs. Self-reflection at session end keeps the index growing even without realized returns. | Self-reflection without realized P&L is degraded; lessons may be miscalibrated. Mitigated by also wiring `reflect_and_remember` for users who do supply `returns_losses`. |
| **G2** Same `(ticker, date)` re-runs from scratch | C (analyst report cache) + R/K injection (so the *non-cached* nodes are also faster because they need less re-derivation) | Exact-key cache guarantees idempotent re-runs at near-zero cost on the analyst tier. The PM/RM/risk debate still re-runs, which is the *right* default (the user typically wants a fresh decision narrative even on cached evidence). | Cache poisoning if a buggy run writes a bad report. Mitigated by storing model + timestamp; an `--invalidate` CLI is trivial. |
| **G3** Stable ticker facts re-derived | K (ticker knowledge card injected via `build_instrument_context`) | The card is read by every node that already calls `build_instrument_context`, so all four analysts and all downstream nodes start with shared baseline facts. The card is updated each session so it stays current. | First-ever run on a new ticker has an empty card. Acceptable cold-start cost. |
| **G4** Macro re-fetched per ticker per day | M (daily macro notes, cross-ticker) | News Analyst and PM read the same recent-weeks block; News Analyst can skip `get_global_news` if a fresh macro note exists. Multi-ticker scripts share one macro refresh per process. | Macro note is one LLM-summarized step removed from raw news. Mitigated by also keeping the source URLs in the bullet. |
| **G5** PM rates without portfolio context | U (portfolio + profile injected at Trader and PM) | Both decision-making nodes see exact holdings and constraints. Read-only ⇒ agent cannot accidentally corrupt user state. | Agent might still ignore the constraint. The prompt block is explicit ("HARD CONSTRAINTS — do not propose actions that violate these"). Refine phase can stress-test. |
| **G6** Decision drift not detected | D (last-3 decisions injected at RM and PM) | RM and PM see the recent thesis sequence and can either reaffirm or explicitly justify a change. | Doesn't prevent drift, only surfaces it. Acceptable: the agent is supposed to be allowed to change its mind given new data. |
| **G7** Tool-output insights lost | **Not addressed in v1.** | Out of scope: requires hooking the `ToolNode` and adds non-trivial complexity. The data already lives in the per-run JSON log so a future refine phase can mine it. | Will be revisited if Critic identifies tool-quality regressions. |
| **G8** Stale memory dir | All types reuse the existing paths under `~/.tradingagents/memory/`. | Existing `NVDA.md`, `NVDA.jsonl`, `portfolio.json`, `watchlist.json` will be picked up by the new code on first run. No data loss; no orphan dir. | If the schema of the previous attempt diverges from this design at the file level, a small migration will be needed at implement time. |

---

## 5. Concrete Integration Points

These are pointers for the implement phase — *what* changes, not *how*.

| File | Change |
|------|--------|
| `tradingagents/agents/utils/memory.py` | Extend `FinancialSituationMemory.__init__` to read `~/.tradingagents/memory/agent/reflections/<name>.jsonl` and rebuild BM25; extend `add_situations` to append to the file. Cap N=500 lines. |
| `tradingagents/agents/utils/agent_utils.py` | `build_instrument_context(ticker)` — append the body of `~/.tradingagents/memory/agent/ticker_knowledge/<TICKER>.md` if present. |
| `tradingagents/graph/setup.py` | For each of the four analyst nodes, wrap with a small `analyst_cache_or_run` decorator that checks Type C cache before invoking the LLM. |
| `tradingagents/graph/trading_graph.py` | After `_log_state()` in `propagate()`: (a) write Type D record from `final_trade_decision`; (b) call a `KnowledgeCardWriter.merge(state)` for Type K; (c) call a `SelfReflector.run(state)` for Type R. |
| `tradingagents/agents/analysts/news_analyst.py` | Before LLM, call a `MacroNotes.refresh_if_stale(trade_date)` then inject the latest two weeks into the system prompt. |
| `tradingagents/agents/managers/research_manager.py` | Read last-3 decisions for the ticker; inject as a `## Prior decisions` block. |
| `tradingagents/agents/managers/portfolio_manager.py` | Inject (a) Type D last-3 decisions, (b) Type U portfolio + profile, (c) Type M macro block. |
| `tradingagents/agents/trader/trader.py` | Inject Type U portfolio + profile. |
| **New** `tradingagents/agents/utils/memory_stores.py` | `KnowledgeCard`, `DecisionsLog`, `MacroNotes`, `AnalystCache`, `UserContext` — each a small dataclass with `read()` / `write()` methods over the paths above. No external dependencies. |
| **New** `tradingagents/graph/self_reflection.py` | `SelfReflector` that mirrors `Reflector` but uses a "no realized returns" prompt. |

No change required to:
- `run.py` — public API is preserved.
- `propagation.py`, `conditional_logic.py` — graph topology is unchanged.
- The five debate / risk / debator nodes — they continue to use `FinancialSituationMemory` unchanged (it's just no longer empty).

---

## 6. Open Questions (defer to implement / refine)

1. **Self-reflection prompt quality** — how aggressive should the
   without-returns reflection be? Risk of fabricating "lessons" from a
   single session. Suggested implement-phase mitigation: only emit a
   reflection record if it cites a *specific* contradiction or risk that
   was explicitly raised by Bear/Conservative and ignored by PM.
2. **N=500 BM25 cap** — chosen by intuition. Revisit when Critic shows
   relevance degradation on long-tail eviction.
3. **Cache invalidation** — should `model` change in the config
   automatically invalidate Type C cache? Lean: yes. Implement detail:
   include `model` in the cache key path or in a sidecar metadata file.
4. **Multi-ticker watchlist runs** — the existing
   `~/.tradingagents/memory/user/watchlist.json` suggests batch runs.
   Out of scope for the design; the building blocks (per-ticker K, D, C,
   shared M, shared U) already support batch use without further changes.
5. **Concurrency** — if two `propagate()` calls run in parallel for
   different tickers, R/K/D writes are file-local per ticker (safe);
   M writes need a simple file lock or single-writer convention.
6. **Cleanup of the previous attempt's `agent/recommendations/NVDA.jsonl`**
   — its schema includes `key_risks` strings that look truncated. Decide
   at implement time whether to migrate or wipe.

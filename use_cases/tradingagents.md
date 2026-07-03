# TradingAgents Memory Use Case

This document summarizes the long-term memory added to TradingAgents and the memory-behavior benchmark results from `TradingAgents/bench`.

## Memory Integration Flow

TradingAgents is a LangGraph multi-agent trading pipeline. Each `propagate(ticker, trade_date)` call runs market, social, news, and fundamentals analysts, then a bull/bear debate, research manager, trader, risk debate, and portfolio manager. Before memory, the graph kept its working state in RAM and cleared large tool-message windows after each analyst; across runs, the agent could not reuse prior lessons, ticker context, decisions, or macro summaries.

The memory integration keeps durable agent memory under `~/.tradingagents/memory/` in normal runs, while the StockBench benchmark redirects the same stores to `bench/stockbench/runs/...` so memory-on and memory-off can be compared without touching a user's real memory.

```text
                         +--------------------------------------+
                         | TradingAgentsGraph.propagate(ticker) |
                         +------------------+-------------------+
                                            |
                 pre-graph macro refresh   |   per-node prompt assembly
                                            v
+---------------------------+   +-----------+-----------+   +---------------------------+
| MacroNotes                |-->| Analyst / debate / PM |<--| UserContext + Decisions   |
| agent/macro_notes.md      |   | LangGraph nodes        |   | user/ + recommendations/  |
+---------------------------+   +-----------+-----------+   +---------------------------+
                                            |
                          analyst cache hit | analyst cache miss
                                            v
                         +------------------+------------------+
                         | AnalystCache exact-key reports       |
                         | agent/analyst_cache/<TICKER>/<DATE> |
                         +------------------+------------------+
                                            |
                                post-graph writes
                                            v
+---------------------------+   +-----------+-----------+   +---------------------------+
| KnowledgeCard             |<--| DecisionsLog +        |-->| BM25 reflection buckets   |
| ticker_knowledge/<T>.md   |   | SelfReflector         |   | reflections/*.jsonl       |
+---------------------------+   +-----------------------+   +---------------------------+

High-level flow:
1. Before graph execution, `MacroRefresher.refresh_if_stale()` can update weekly macro notes once per process.
2. At analyst nodes, exact `(ticker, trade_date, analyst_type)` cache hits bypass repeated LLM/tool work; misses run normally and then write the cache.
3. During prompt assembly, TradingAgents injects ticker knowledge cards, prior decisions, user portfolio/profile context, macro notes, and BM25 reflection recall at the existing role-specific nodes.
4. After `propagate()`, the system appends the current decision, merges the ticker knowledge card, and runs self-reflection to add situation/lesson pairs to persistent BM25 buckets. The existing `reflect_and_remember(returns_losses)` path remains available for outcome-aware reflection.
```

## Memory Types

| Memory type | Storage | Write timing and method | Read timing and method | Token / size limit |
| --- | --- | --- | --- | --- |
| BM25 reflection lessons | `agent/reflections/{bull,bear,trader,invest_judge,portfolio_manager}.jsonl` | Written after `propagate()` by `SelfReflector.run()` and by the existing `reflect_and_remember(returns_losses)` outcome path. Each line stores a situation and recommendation; `FinancialSituationMemory.add_situations()` persists to disk. | Read at the top of Bull, Bear, Research Manager, Trader, and Portfolio Manager nodes through the existing BM25 `get_memories()` call sites. | Ring buffer capped at 500 records per bucket. Recall injects only top matches, normally `n_matches=2`. |
| Ticker knowledge card | `agent/ticker_knowledge/<TICKER>.md` | Written after `propagate()` by `KnowledgeCardWriter.merge()`, which merges the latest reports and final rating into stable fundamentals, recent technical posture, and recent PM ratings. | Read by `build_instrument_context(ticker)` and injected into every node that already asks for instrument context. | Bounded compact markdown card, roughly 1.5-2 KB in the design. |
| Decisions log | `agent/recommendations/<TICKER>.jsonl` | Appended after `propagate()` by `DecisionsLog.append()`, extracting date, ticker, rating, one-line thesis, and key risks from the final decision. Same-date writes overwrite the previous record. | Research Manager and Portfolio Manager read the last 3 sorted decisions as a "Prior decisions" prompt block. | JSONL capped at 500 records per ticker; recall is only a small tail slice. |
| User portfolio/profile context | `user/portfolio.json`, `user/profile.md` | Not written by the agent. The user edits holdings, cash, risk tolerance, time horizon, and constraints manually. | Trader and Portfolio Manager read current ticker position plus profile text through `UserContext.read(ticker)`. | Small read-only prompt block; no agent write path. |
| Macro notes | `agent/macro_notes.md` | `MacroRefresher.refresh_if_stale(trade_date)` summarizes global news into an ISO-week section before graph execution when stale. Writes are once per process. | News Analyst and Portfolio Manager read the most recent sections as macro context. | Rolling cap of 8 weekly sections; read usually returns last 2. |
| Analyst report cache | `agent/analyst_cache/<TICKER>/<DATE>/{market,social,news,fundamentals}.json` | Written deterministically after each analyst completes a report. | Exact-key cache check at the start of each analyst node. A hit skips that analyst's LLM and tool loop and returns the cached report into graph state. | No prompt cost on hit beyond downstream use of the report. Delete the date folder to invalidate. |

The implementation stayed light-to-medium weight: file-backed stores, local BM25, exact-key caches, and no embeddings or external database. Refine testing built 17 traces across knowledge/reflection, analyst cache, decisions log, and macro paths; by Phase 5 all 17 component traces scored 1.0, with fixes for macro parsing, BM25 situation wording, knowledge-card cleanup, date-sorted decision reads, zero-count edge cases, ring-buffer eviction, and out-of-order macro section sorting.

## Benchmark Setup

The benchmark in `TradingAgents/bench` is an offline StockBench memory-behavior harness. It is explicitly **not** a trading-performance benchmark: StockBench can support numeric backtests, but TradingAgents emits narrative reports plus coarse rating labels rather than fully specified trade sizing and execution targets. The canonical report is `TradingAgents/bench/results.md`.

| Condition | Meaning |
| --- | --- |
| `memory-on` | All symbols and dates share a persistent memory root at `stockbench/runs/memory-on/shared-memory/`, including reflections, knowledge cards, decisions, macro notes, and analyst cache. |
| `memory-off` | Each `(date, symbol)` decision uses an isolated memory root under `session-*/memory/isolated/<date>-<SYMBOL>/`, suppressing cross-day and cross-symbol recall. |

Important bench details:

| Artifact / script | Role |
| --- | --- |
| `bench/harness.py` | CLI entry for dry-run or full StockBench execution. It patches TradingAgents memory roots only inside the bench process. |
| `bench/stockbench_adapter.py` | Offline StockBench OHLCV adapter and data-route patching; avoids live data calls. |
| `bench/stockbench_portfolio.py` | Multi-day `propagate()` loop over symbols and dates; writes decisions, full-state logs, memory snapshots, and optional outcome-aware reflection flushes. |
| `bench/metrics.py` | Generates `bench/results.md` from existing artifacts. It reports memory coverage and qualitative evidence only. |
| `bench/construction.md` | Documents data layout, run commands, memory-on/off protocol, and the no-P&L benchmark contract. |

The run summarized here uses one StockBench session with three symbols (`AAPL`, `AMZN`, `MSFT`) across the June 2025 window. Memory-on keeps a shared root; memory-off isolates every decision.

## Benchmark Results

### StockBench Memory Coverage

| Area | Memory-on evidence |
| --- | --- |
| Knowledge card injection | `kc_in_prompt: true`; AAPL card length is 1,764 characters. |
| Recent decision continuity | `decisions_log_count: 10` in the memory snapshot; each of AAPL, AMZN, and MSFT has 19 recommendation records. |
| Reflection corpus | Five JSONL reflection buckets (`bull`, `bear`, `trader`, `invest_judge`, `portfolio_manager`) each contain 175 items. |
| Cross-ticker shared stores | Shared memory contains knowledge cards for AAPL, AMZN, and MSFT; one macro note file of 5,880 characters; and 228 analyst-cache JSON files. |

These numbers show that the memory-on run populated all intended store families: R (reflections), K (ticker knowledge), D (decisions), M (macro notes), and C (analyst cache). The report does not claim that these stores improved returns or predictive accuracy.

### Qualitative Case 1: Cross-ticker Lesson Recall

In `AAPL` on `2025-06-09`, the full-state log shows the judge explicitly using a past Microsoft mistake as decision context:

> Looking at this debate and reflecting on my past mistakes with Microsoft, I'm aligning decisively with the bear analyst and recommending a SELL on AAPL.

This is the core memory behavior the bench is meant to expose: prior lessons are available in later reasoning and can be applied across tickers, rather than disappearing after the original run.

### Qualitative Case 2: Prior Mistake Reused on MSFT

In `MSFT` on `2025-06-27`, the judge frames the decision through an earlier incorrect sell decision:

> I'm looking at this debate through the lens of the hard lessons from my past mistakes, specifically the incorrect sell decision I made back in June when faced with nearly identical conditions.

This case demonstrates a later decision using durable lesson language from the reflection/decision history. The benchmark treats it as behavioral evidence of memory persistence and recall, not as an outcome claim.

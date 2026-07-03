# Dedupe debug `pretty_print` for unchanged `messages[-1]`

## Summary

When `TradingAgentsGraph.propagate(..., debug=True)` streams the graph, it prints `chunk["messages"][-1]` on every chunk. Many nodes update only structured fields (e.g. debate / risk state) and **do not append to `messages`**, so `messages[-1]` stays the same for many steps—either the placeholder `HumanMessage("Continue")` after a Msg Clear node or the Trader’s last `AIMessage`. That produces **spammy, duplicated terminal output** that looks like repeated decisions but is only a logging artifact.

This change **deduplicates by message identity** and calls `pretty_print` only when the last message actually changes.

## Problem

- `stream_mode="values"` emits a full state after each step.
- The debug loop always printed the last message in `messages`, even when that message was identical to the previous step.
- Result: many consecutive lines of `Continue` or the same Trader block, which is confusing and noisy.

## Solution

Track a fingerprint for the last message and skip printing when it matches the last printed fingerprint:

- Prefer LangChain `message.id` when present.
- Otherwise use `(message class name, content)`; non-string `content` is normalized via `repr`.

## Implementation

- **File:** `tradingagents/graph/trading_graph.py`
- **Location:** `propagate()` → `if self.debug:` branch (`_last_msg_key`, `last_printed_key`).


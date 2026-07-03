# Browser-Use Memory Use Case

This document summarizes the supplemental cross-session memory added to Browser-Use and the benchmark results from `browser-use/bench`.

## Memory Integration Flow

Browser-Use is a browser automation agent. Each `Agent.run()` starts or attaches to a `BrowserSession`, builds a current state message from browser tabs, DOM, screenshot, agent history, and optional read state, asks an LLM for structured actions, executes browser tools, and records an `AgentHistory` item. Before this memory layer, Browser-Use had useful in-run memory, but a new agent session did not automatically reuse prior trajectories, extracted answers, or site-specific failure experience.

The memory integration keeps durable records in a local `BrowserUseMemory` store. The store is backed by SQLite plus raw trace JSON files, and it is enabled only when `Agent(memory=True)`, `Agent(memory_path=...)`, `Agent(memory=<BrowserUseMemory>)`, or `BROWSER_USE_MEMORY_DIR` is configured.

```text
                         +-----------------------------+
                         | Browser-Use Agent.run loop  |
                         | browser_use/agent/service   |
                         +-------------+---------------+
                                       |
                    prompt assembly   |   step history / action results / final history
                                       v
+---------------------------+   +------+--------------------+   +---------------------------+
| <memory_context>          |<--| BrowserUseMemory          |-->| SQLite memories table     |
| injected before browser   |   | browser_use/memory        |   | kind + dedupe_key + JSON  |
| state                     |   +------+--------------------+   +-------------+-------------+
+---------------------------+          |                                      |
                                       |                                      |
                                       v                                      v
                         +-------------+-------------+        +---------------+--------------+
                         | Model decisions + tools   |        | shortcuts / caches /        |
                         | navigate, click, extract  |        | site notes / snapshots /    |
                         +---------------------------+        | trace archive               |
                                                              +------------------------------+

High-level flow:
1. Before each LLM decision, `Agent._render_memory_context()` calls `BrowserUseMemory.recall(task, current_url)` and injects a compact advisory `<memory_context>` block into the state message.
2. After each finalized step, `Agent._record_memory_step()` writes task snapshots and site-friction notes from structured action results.
3. At run cleanup, `Agent._commit_memory_history()` archives the raw trace and extracts shortcuts, obtained-information caches, site notes, and final task state.
4. Future sessions can reuse the records as navigation and verification hints, while the prompt explicitly requires current-page/source verification before final reporting.
```

## Memory Types

| Memory type | Storage | Write timing and method | Read timing and method | Token / size limit |
| --- | --- | --- | --- | --- |
| Navigation shortcut memory | SQLite `memories` rows with `kind='shortcut'`; each JSON record stores `task_intent`, `domain_scope`, `start_conditions`, `route_steps`, semantic anchors, fingerprints, success condition, source trace id, and success/failure counts. | Written at successful session flush by `BrowserUseMemory.commit_history()` and `flush_window()`. The extractor converts action history into semantic route steps and rejects shortcuts without domains, success conditions, or usable anchors for click-like steps. | Read before each LLM step through `memory.recall()`, ranked by task terms, domain match, and success/failure counts. The prompt formats at most 3 shortcuts and only the first 6 route steps per shortcut. | Design target is about 1,000 characters per shortcut. Implementation caps recall to 3 shortcuts and formats compact route/success-condition text, not raw DOM or screenshots. |
| Obtained information cache | SQLite `memories` rows with `kind='cache'`; JSON stores query signature, answer, source URL, source snippet, observed time, freshness policy, confidence, verification recipe, and source trace id. | Written at session flush from successful `done` output and useful extracted content. Answers are deduped by query signature + source URL + normalized answer. LLM extraction can add compact cache drafts, but rigorous extraction works without it. | Read before each LLM step through `memory.recall()`, ranked by query/domain. Non-volatile entries are shown as cached answers plus verification recipes; volatile entries are shown as verification routes without reusing stale answers. | Recall returns at most 5 cache entries. Implementation truncates stored answer to 1,200 characters and source snippet to 500 characters; the design target is about 700 characters per injected cache. |
| Site friction and recovery memory | SQLite `memories` rows with `kind='site_note'`; JSON stores site pattern, problem type, symptom, failed actions, optional successful recovery, applicability, and success/failure counts. | Written after each step from `ActionResult.error` and `long_term_memory`, and also during session extraction. The classifier recognizes readiness timeouts, captcha/cloudflare, blocked pages, failed clicks, selector/index instability, empty DOM, PDF viewer, login redirects, and other. | Read when task/current URL matches a domain. The prompt formats at most 3 relevant site notes as short operational warnings separate from facts. | Recall limit is 3 notes. Symptoms are stored up to 1,000 characters and parsed recovery text up to 500 characters. |
| Task progress snapshot | SQLite `memories` rows with `kind='task_snapshot'`; JSON stores task/session id, original task, current goal, completed/pending items, current URL/tabs, available files, last successful action, blocking issue, and archived flag. | Written after each step and at session flush. Repeated writes merge completed items and artifacts under the task id; successful final snapshots clear pending goals and are archived. | Read only when `include_snapshots=True`, intended for resume/follow-up tasks rather than ordinary fresh tasks. Search uses task/session ids and resume-like query terms. | Recall returns at most 1 snapshot. Stored completed items keep a bounded recent list; prompt formatting uses a head-plus-tail display when long. |
| Session trace archive | SQLite `memories` rows with `kind='trace'` plus raw JSON files under `<memory_dir>/traces/`. JSON stores task, success, final result, URLs, action names, compact summary, raw trace path, and redaction flag. | Written on every session flush before higher-level extraction. It preserves evidence for debugging and later memory repair even when a run failed or produced no reusable fact. | Not injected into normal prompts. It is read through `BrowserUseMemory.read_traces()` or by debugging/evaluation scripts when shortcut/cache/site-note evidence is insufficient. | No ordinary prompt cost. Raw traces are kept out of `<memory_context>`; only compact summaries are indexed. |

The SQLite store uses one durable table:

```text
memories(kind, id, dedupe_key, search_text, data, created_at, updated_at)
```

`data` is validated JSON for the memory type, while `dedupe_key` gives deterministic merge/update semantics. Retrieval is lightweight lexical/domain scoring; no embedding index is required.

## Benchmark Setup

The benchmark in `browser-use/bench` is a supplemental-memory showcase harness. It compares two conditions:

| Condition | Meaning |
| --- | --- |
| `memory-on` | Tasks share one initially empty `BrowserUseMemory` store under `bench/results/<timestamp>/github/memory_store/on`. Memory accumulates across tasks and across rounds. |
| `memory-off` | Every task runs with `memory=None`, so no supplemental cross-session recall is available. |

The run summarized here is `bench/results/20260512T053844Z/summary.md`. It uses the `github` showcase: 10 repeated information-seeking tasks against `https://github.com/browser-use/browser-use`, repeated for 3 rounds in both modes. The 3-round setup is used because Browser-Use with the qwen-flash model is not fully stable run-to-run; repeated rounds reduce the chance that one lucky or unlucky trajectory dominates the comparison.
The 10 GitHub tasks are built in `github_tasks()` as plain-language questions:
```text
1. For the browser-use github repo, how many stars and forks does it have, and what is the default branch?
2. On the Issues page for browser-use github repo, how many issues are open?
3. Open the first issue in browser-use github repo and tell me its title, author, and labels.
4. Open the second issue in browser-use github repo and tell me its title, author, and labels.
5. In browser-use github repo, search issues for the keyword memory and give the titles of the first three results.
6. On the Pull requests page for browser-use github repo, how many pull requests are open?
7. Open the first pull request in browser-use github repo and tell me its title, checks or CI status, and author.
8. On the Actions tab for browser-use github repo on github, what is the latest workflow run called and did it succeed or fail?
9. What command does the README give to install the browser-use github repo package?
10. In browser-use github repo, find the examples or docs examples area and name one getting-started-style example file.
```
Each task runs as a separate Browser-Use agent session with its own browser profile directory. In `memory-on`, all 30 sessions share the same supplemental memory store, so round 1 populates memory and rounds 2-3 can recall it. In `memory-off`, every session is memory-free, so any improvement across rounds can only come from incidental model behavior or live site state, not durable cross-session memory.

Important bench details:

| Artifact / script | Role |
| --- | --- |
| `bench/supplemental_memory_showcases.py` | CLI entry point. It builds GitHub and local failure-reuse task suites, runs memory-on/off modes, and writes metrics. |
| `bench/README.md` | Documents showcase commands, modes, outputs, and demo claims. |
| `bench/results/20260512T053844Z/summary.csv` | Per-run metrics table used to generate the Markdown summary. |
| `bench/results/20260512T053844Z/summary.md` | Canonical compact comparison for this run. |
| `bench/results/20260512T053844Z/github/memory_store/on/traces/*.json` | Archived memory-on traces. The retained snapshot did not include the SQLite file itself, but these traces contain the exact `<memory_context>` blocks recalled from the store before model decisions. |

Evaluation records per-task steps, total/input/output tokens, clicks, extract/search calls, duration, whether the agent marked the task successful, and which memory blocks were retrieved. GitHub values are live and can change, so the intended memory behavior is not blind answer replay. Memory should give navigation shortcuts, cached source-grounded context, and verification recipes; the agent still needs to verify current page state before reporting.

## Benchmark Results

### GitHub Showcase Totals

| Condition | Accuracy | Steps | Total tokens | Input tokens | Output tokens | Clicks | Extract/search calls | Duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| memory-on | 30 / 30 | 105 | 1,890,296 | 1,752,005 | 138,291 | 33 | 3 | 911.35s |
| memory-off | 29 / 30 | 187 | 3,210,336 | 3,125,715 | 84,621 | 88 | 15 | 1,385.07s |

Memory-on solved every task and reduced total steps by about 44%, total tokens by about 41%, clicks by about 63%, extract/search calls by 80%, and duration by about 34%. The only failed task was memory-off round 3 for `issues-search-memory`.

Round-level numbers show the cross-session effect after the memory store has been populated:

| Condition | Round | Accuracy | Steps | Total tokens | Clicks | Extract/search calls | Duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| memory-on | 1 | 10 / 10 | 44 | 853,968 | 16 | 2 | 359.15s |
| memory-on | 2 | 10 / 10 | 31 | 525,451 | 8 | 1 | 261.25s |
| memory-on | 3 | 10 / 10 | 30 | 510,877 | 9 | 0 | 290.96s |
| memory-off | 1 | 10 / 10 | 66 | 1,202,427 | 30 | 4 | 496.58s |
| memory-off | 2 | 10 / 10 | 57 | 933,218 | 30 | 1 | 435.94s |
| memory-off | 3 | 9 / 10 | 64 | 1,074,691 | 28 | 10 | 452.55s |

### SQLite Memory Evidence and Cross-Session Examples

The retained run snapshot no longer contains `browser_use_memory.sqlite3`, but the memory-on raw traces preserve recalled `<memory_context>` blocks that were generated from SQLite records. Those blocks show the practical records that mattered during the run: navigation shortcuts, source-grounded cache entries, and site notes.

| Example | Stored / recalled memory | memory-on behavior | memory-off contrast |
| --- | --- | --- | --- |
| Issue search for `memory` | The recalled cache stored: query "search issues for the keyword memory", answer "531 issues (10 open + 521 closed)" and the first three issue titles, source `https://github.com/browser-use/browser-use/issues?q=memory`, observed time, slow-changing freshness, and a verification recipe. The shortcut told the agent to reach the GitHub issue search page and verify page content. | Rounds 2 and 3 completed in 2 steps with 36,234 and 36,362 tokens, 0 clicks, and 0 extract/search calls after navigating directly to the filtered URL and verifying visible results. | Round 1 needed 25 steps and 517,790 tokens. Round 3 again spent 25 steps, 509,493 tokens, 14 clicks, and 7 extract/search calls, then failed after repeated search/filter attempts did not produce the expected result list. Memory-on avoided repeating that GitHub search UI failure mode. |
| Latest Actions workflow | The recalled shortcut stored the route "navigate to `https://github.com/browser-use/browser-use/actions` -> capture top workflow run name and status". Because the task asks for latest/current status, memory treated it as a verification route rather than a stale final answer. | Memory-on completed the Actions task in 2, 3, and 2 steps across rounds, with 0 clicks each time and 31k-42k tokens. It used memory to go directly to the Actions tab and then read the live top workflow. | Memory-off round 2 needed 16 steps, 239,351 tokens, 12 clicks, and 1 extract/search call before answering. Without the remembered route, the agent wandered through the page UI much more heavily. |
| First pull request details | The recalled memory included multiple shortcuts for the PR flow: go to `/pulls`, open the first PR, use the Checks tab, and verify the CI status. Cache entries included PR `#4819`, title, author `paulxg12`, and "All 87 checks passed", with source URLs on the PR and Checks pages. | Memory-on consistently used 5 steps for this task and verified the current PR/checks page before reporting. The recalled context gave a concrete target and success condition, so the agent did not need to rediscover the Checks path from scratch. | Memory-off needed 7, 6, and 10 steps, with more clicks and one extract/search call in round 3. It still often succeeded, but spent more browser actions rediscovering where the CI/checks status lived. |

These examples show the intended cross-session experience: memory is strongest when it encodes a verified route and source-grounded answer shape, then asks the browser agent to re-check current page state. It reduces repeated navigation, preserves stable facts like README/example paths, and helps avoid known failure loops such as GitHub issue search interactions, without treating live GitHub counts or workflow status as immutable.

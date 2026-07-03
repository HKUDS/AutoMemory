#!/usr/bin/env python3
"""Run ESL-Bench nanobot drivers in order: ``run_agent.py`` then ``eval_agent.py``.

Re-invokes each script with the **same trailing arguments** (e.g. ``--workspace …``,
``--timeout 600``). Injects a **shared** ``--session`` for both phases unless ``--session``
is already present (then that value is reused for both).

Per-message ``NANOBOT_CURRENT_TIME`` during ingest is computed in ``bench/esl_turn_timing.py``
(used by ``run_agent.py`` and by ``eval_agent.py`` when ``--ingest-mixed``).

Progress: phase banners on stderr.

Example::

    cd /path/to/nanobot-health
    python3 bench/run_esl_bench.py --workspace ~/.nanobot/workspace
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

from esl_turn_timing import ingest_time_rules_summary


def _strip_session(tail: list[str]) -> tuple[str | None, list[str]]:
    sess: str | None = None
    out: list[str] = []
    i = 0
    while i < len(tail):
        if tail[i] == "--session" and i + 1 < len(tail):
            sess = tail[i + 1]
            i += 2
            continue
        out.append(tail[i])
        i += 1
    return sess, out


def main() -> int:
    bench = Path(__file__).resolve().parent
    argv = sys.argv[1:]
    eval_session_override = ""
    stop_on_run_fail = False
    filtered: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--eval-session" and i + 1 < len(argv):
            eval_session_override = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--stop-on-run-fail":
            stop_on_run_fail = True
            i += 1
            continue
        filtered.append(argv[i])
        i += 1

    user_sess, tail_no_sess = _strip_session(filtered)
    shared = (
        eval_session_override.strip()
        or (user_sess.strip() if user_sess else "")
        or f"bench:esl:run:{uuid.uuid4().hex[:10]}"
    )
    run_cmd_tail = ["--session", shared] + tail_no_sess
    eval_cmd_tail = ["--session", shared] + tail_no_sess

    def _phase(title: str, script: str, tail: list[str]) -> int:
        bar = "=" * 72
        print(f"\n{bar}\n[run_esl_bench] PHASE: {title}\n{bar}\n", file=sys.stderr, flush=True)
        cmd = [sys.executable, str(bench / script)] + tail
        print(f"[run_esl_bench] command:\n  {' '.join(cmd)}\n", file=sys.stderr, flush=True)
        return subprocess.run(cmd, cwd=str(bench.parent)).returncode

    print(f"[run_esl_bench] shared nanobot session: {shared!r}\n", file=sys.stderr, flush=True)
    print(f"[run_esl_bench] {ingest_time_rules_summary()}\n", file=sys.stderr, flush=True)

    rc_run = _phase("run_agent (ingest user turns)", "run_agent.py", run_cmd_tail)
    if rc_run != 0 and stop_on_run_fail:
        print("[run_esl_bench] stopping: run_agent failed", file=sys.stderr, flush=True)
        return rc_run

    rc_eval = _phase("eval_agent (QA + scoring)", "eval_agent.py", eval_cmd_tail)
    print(
        f"\n[run_esl_bench] DONE run_agent={rc_run} eval_agent={rc_eval}\n",
        file=sys.stderr,
        flush=True,
    )
    if rc_eval != 0:
        return rc_eval
    return rc_run


if __name__ == "__main__":
    raise SystemExit(main())

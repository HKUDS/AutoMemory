#!/usr/bin/env python3
"""Replay ESL-Bench ``user_turn_mixed.json`` user messages through nanobot (CLI).

Each ``user`` message is sent as ``nanobot agent -m <content>`` with a fixed session so
the agent accumulates context. ``NANOBOT_CURRENT_TIME`` per message follows
``bench/esl_turn_timing.py`` (merge-day profile +30m from 11:00, timeline +1h steps, etc.).
By default assistant output is streamed directly to the terminal. Pass ``--run-result-dir``
only when you also want JSONL logging.

Progress: prints ``[run_agent] i/total …`` on stderr.

Example::

    cd /path/to/nanobot-health
    python3 bench/run_agent.py --session bench:esl:ingest1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from esl_turn_timing import iter_user_messages_with_current_time
from reply_sanitize import clean_captured_stdout


def default_repo_root() -> Path:
    """``nanobot-health`` repo root (parent of ``bench/``)."""
    return Path(__file__).resolve().parent.parent


def run_nanobot_agent_message(
    message: str,
    *,
    session_id: str,
    repo_root: Path,
    workspace: Path | None,
    config: Path | None,
    capture_output: bool,
    timeout_s: float | None,
    current_time: str | None = None,
    no_markdown: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd: list[str] = [
        sys.executable,
        "-m",
        "nanobot",
        "agent",
        "-m",
        message,
        "--session",
        session_id,
    ]
    if no_markdown:
        cmd.append("--no-markdown")
    if workspace is not None:
        cmd.extend(["--workspace", str(workspace)])
    if config is not None:
        cmd.extend(["--config", str(config)])
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    env.setdefault("FORCE_COLOR", "0")
    if current_time:
        env["NANOBOT_CURRENT_TIME"] = current_time
    return subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=capture_output,
        text=True,
        timeout=timeout_s,
        env=env,
    )


def _default_mixed_path(bench_dir: Path) -> Path:
    return bench_dir / "ESL-Bench" / "data" / "evaluation" / "user_turn_mixed.json"


def _append_reply_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    bench_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mixed-json",
        type=Path,
        default=_default_mixed_path(bench_dir),
        help="Path to user_turn_mixed.json",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=default_repo_root(),
        help="nanobot-health repo root (cwd for ``python -m nanobot``)",
    )
    parser.add_argument("--workspace", type=Path, default=None, help="nanobot -w workspace")
    parser.add_argument("--config", type=Path, default=None, help="nanobot -c config path")
    parser.add_argument(
        "--session",
        type=str,
        default="",
        help="nanobot --session (default: bench:esl:ingest:<uuid>)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Per-message subprocess timeout (seconds)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Seconds to sleep after each message (rate limit)",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=0,
        help="Process at most N user messages (0 = all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned messages only, do not call nanobot",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress assistant terminal output",
    )
    parser.add_argument(
        "--run-result-dir",
        type=Path,
        default=None,
        help="If set, append each assistant reply to <dir>/nanobot_replies.jsonl",
    )
    parser.add_argument(
        "--no-run-result",
        action="store_true",
        help="Disable writing nanobot_replies.jsonl even if --run-result-dir is set",
    )
    parser.add_argument(
        "--fresh-run-results",
        action="store_true",
        help="Truncate nanobot_replies.jsonl before this run (only when run-result is enabled)",
    )
    args = parser.parse_args()

    session = args.session.strip() or f"bench:esl:ingest:{uuid.uuid4().hex[:12]}"
    mixed_path = args.mixed_json.resolve()
    if not mixed_path.is_file():
        print(f"[run_agent] ERROR: mixed json not found: {mixed_path}", file=sys.stderr)
        return 1

    data = json.loads(mixed_path.read_text(encoding="utf-8"))
    items = iter_user_messages_with_current_time(data)
    if args.max_messages:
        items = items[: args.max_messages]
    total = len(items)
    if total == 0:
        print("[run_agent] ERROR: no user messages in mixed file", file=sys.stderr)
        return 1

    replies_path = None
    if args.run_result_dir is not None and not args.no_run_result:
        result_dir = args.run_result_dir.resolve()
        replies_path = result_dir / "nanobot_replies.jsonl"
        if args.fresh_run_results and replies_path.is_file():
            replies_path.write_text("", encoding="utf-8")

    print(
        f"[run_agent] start session={session!r} messages={total} mixed={mixed_path}"
        + (f" replies_log={replies_path}" if replies_path else ""),
        file=sys.stderr,
        flush=True,
    )

    for idx, (label, content, current_time) in enumerate(items, start=1):
        time_suffix = f" time={current_time}" if current_time else ""
        print(f"[run_agent] {idx}/{total} {label} ({len(content)} chars){time_suffix}", file=sys.stderr, flush=True)
        if args.dry_run:
            continue
        need_capture = bool(replies_path) or args.quiet
        cp = run_nanobot_agent_message(
            content,
            session_id=session,
            repo_root=args.repo_root.resolve(),
            workspace=args.workspace.resolve() if args.workspace else None,
            config=args.config.resolve() if args.config else None,
            capture_output=need_capture,
            timeout_s=args.timeout,
            current_time=current_time,
        )
        reply_raw = (cp.stdout or "").strip() if need_capture else ""
        reply = clean_captured_stdout(reply_raw) if need_capture else ""
        if replies_path is not None:
            _append_reply_jsonl(
                replies_path,
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "script": "run_agent",
                    "phase": "ingest",
                    "session": session,
                    "step": idx,
                    "step_total": total,
                    "turn_label": label,
                    "nanobot_current_time": current_time,
                    "user_message_chars": len(content),
                    "exit_code": cp.returncode,
                    "reply": reply,
                    "stderr_tail": (cp.stderr or "")[-2000:] if cp.stderr else "",
                },
            )
        if not args.quiet and need_capture and reply:
            preview = reply[:800] + ("…" if len(reply) > 800 else "")
            print(f"[run_agent] reply_preview:\n{preview}\n", file=sys.stderr, flush=True)

        if cp.returncode != 0:
            print(
                f"[run_agent] ERROR nanobot exit {cp.returncode} at {idx}/{total}",
                file=sys.stderr,
                flush=True,
            )
            if cp.stderr:
                sys.stderr.write(cp.stderr)
            return cp.returncode or 1
        if args.sleep:
            time.sleep(args.sleep)

    print(f"[run_agent] done session={session!r} ok", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

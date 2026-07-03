#!/usr/bin/env python3
"""Ask each ESL-Bench benchmark question through nanobot and score replies.

1. For each item in ``question_sparse40.json``, sends **only** the question string via
   ``nanobot agent -m`` as one user turn.
2. After every question is answered and logged, writes a **single** ``eval_results.json``
   under ``--run-result-dir``. **Default scoring** uses the LLM judge when ``bench/.env``
   has ``LLM_*`` and ``--no-llm-judge`` is not set: the file uses schema
   ``esl_eval_results_llm_judge_v1``, includes per-item ``llm_judge`` blocks, and
   top-level ``passed`` / ``pass_rate`` follow the LLM verdict (with heuristic fallback
   only if a judge **call** fails). With ``--no-llm-judge`` or missing credentials,
   the same path gets heuristic-only results (``scoring_mode``: ``heuristic_only``).

Optional ``--also-write-eval-results-llm-judge`` duplicates the LLM merge to the legacy
``eval_results_llm_judge.json`` filename for older tooling.

By default writes ``bench/run_result/nanobot_replies.jsonl`` (one JSON per assistant reply)
and ``bench/run_result/eval_results.json``.

Progress output mirrors ``run_agent.py``: ``start``, ``i/total ...``,
``reply_preview``, and ``done`` lines on stderr.

Example::

    cd /path/to/nanobot-health
    python3 bench/eval_agent.py --session bench:esl:eval1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from esl_turn_timing import (
    iter_user_messages_with_current_time,
    last_ingest_nanobot_current_time,
)
from heuristic_score import norm_text, score_answer
from llm_judge_eval import llm_judge_env_ready, run_llm_judge
from reply_sanitize import clean_captured_stdout


def default_repo_root() -> Path:
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


def _append_reply_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _default_mixed_path(bench_dir: Path) -> Path:
    return bench_dir / "ESL-Bench" / "data" / "evaluation" / "user_turn_mixed.json"


def _default_questions_path(bench_dir: Path) -> Path:
    return bench_dir / "ESL-Bench" / "data" / "evaluation" / "question_sparse40.json"


def _turn_current_time_noon(date: str | None) -> str | None:
    return f"{date}T12:00:00" if date else None


def _max_turn_current_time(mixed_path: Path) -> str | None:
    try:
        mixed = json.loads(mixed_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tail = last_ingest_nanobot_current_time(mixed)
    if tail:
        return tail
    dates = [
        turn["date"].strip()
        for turn in mixed.get("turns", [])
        if isinstance(turn, dict) and isinstance(turn.get("date"), str) and turn["date"].strip()
    ]
    return _turn_current_time_noon(max(dates)) if dates else None


def _one_line_preview(s: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", s.strip())
    return text[:limit] + ("..." if len(text) > limit else "")


def main() -> int:
    bench_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mixed-json", type=Path, default=_default_mixed_path(bench_dir))
    parser.add_argument("--questions-json", type=Path, default=_default_questions_path(bench_dir))
    parser.add_argument("--repo-root", type=Path, default=default_repo_root())
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--session", type=str, default="", help="default: bench:esl:eval:<uuid>")
    parser.add_argument(
        "--current-time",
        type=str,
        default="",
        help="Override nanobot Current Time for QA turns (default: last dated turn in --mixed-json)",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--max-questions", type=int, default=0, help="0 = all")
    parser.add_argument(
        "--ingest-mixed",
        action="store_true",
        help="Opt in to replaying user_turn_mixed.json before asking questions",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not echo assistant reply preview to stderr (replies still logged if --run-result-dir)",
    )
    parser.add_argument(
        "--run-result-dir",
        type=Path,
        default=None,
        help="Default <bench>/run_result; writes nanobot_replies.jsonl + eval_results.json",
    )
    parser.add_argument("--no-run-result", action="store_true", help="Do not write run_result files")
    parser.add_argument(
        "--fresh-run-results",
        action="store_true",
        help="Deprecated for eval_agent: run results are cleared by default",
    )
    parser.add_argument(
        "--append-run-results",
        action="store_true",
        help="Append to existing nanobot_replies.jsonl / eval_results.json instead of clearing first",
    )
    parser.add_argument(
        "--no-llm-judge",
        action="store_true",
        help="Skip LLM judge; use heuristic scoring only (eval_results.json, scoring_mode=heuristic_only).",
    )
    parser.add_argument(
        "--also-write-eval-results-llm-judge",
        action="store_true",
        help=(
            "When LLM scoring runs, also write eval_results_llm_judge.json with the same payload "
            "as eval_results.json (legacy duplicate path)."
        ),
    )
    parser.add_argument(
        "--llm-judge",
        action="store_true",
        help="Require LLM judge; exit 1 if bench/.env lacks LLM_MODEL_NAME / LLM_API_KEY / LLM_API_BASE.",
    )
    args = parser.parse_args()

    session = args.session.strip() or f"bench:esl:eval:{uuid.uuid4().hex[:12]}"
    q_path = args.questions_json.resolve()
    if not q_path.is_file():
        print(f"[eval_agent] ERROR: questions json not found: {q_path}", file=sys.stderr)
        return 1

    questions = json.loads(q_path.read_text(encoding="utf-8"))
    if not isinstance(questions, list):
        print("[eval_agent] ERROR questions json must be a list", file=sys.stderr)
        return 1

    repo = args.repo_root.resolve()
    qa_current_time = args.current_time.strip() or _max_turn_current_time(args.mixed_json.resolve())

    result_dir = None
    replies_path = None
    eval_path = None
    if not args.no_run_result:
        result_dir = (args.run_result_dir or (bench_dir / "run_result")).resolve()
        replies_path = result_dir / "nanobot_replies.jsonl"
        eval_path = result_dir / "eval_results.json"
        if not args.dry_run and not args.append_run_results and replies_path.is_file():
            replies_path.write_text("", encoding="utf-8")
        if not args.dry_run and not args.append_run_results and eval_path.is_file():
            eval_path.write_text("", encoding="utf-8")

    if args.ingest_mixed:
        mixed_path = args.mixed_json.resolve()
        if not mixed_path.is_file():
            print(f"[eval_agent] ERROR: mixed json not found: {mixed_path}", file=sys.stderr)
            return 1
        mixed = json.loads(mixed_path.read_text(encoding="utf-8"))
        items = iter_user_messages_with_current_time(mixed)
        print(
            f"[eval_agent] phase=ingest session={session!r} user_messages={len(items)}",
            file=sys.stderr,
            flush=True,
        )
        for idx, (label, content, current_time) in enumerate(items, start=1):
            time_suffix = f" time={current_time}" if current_time else ""
            print(f"[eval_agent] ingest {idx}/{len(items)} {label}{time_suffix}", file=sys.stderr, flush=True)
            if args.dry_run:
                continue
            cp = run_nanobot_agent_message(
                content,
                session_id=session,
                repo_root=repo,
                workspace=args.workspace.resolve() if args.workspace else None,
                config=args.config.resolve() if args.config else None,
                capture_output=True,
                timeout_s=args.timeout,
                current_time=current_time,
            )
            if cp.returncode != 0:
                print(f"[eval_agent] ERROR ingest exit {cp.returncode}", file=sys.stderr)
                if cp.stderr:
                    sys.stderr.write(cp.stderr)
                return cp.returncode or 1
            reply_raw = (cp.stdout or "").strip()
            reply = clean_captured_stdout(reply_raw)
            if replies_path is not None:
                _append_reply_jsonl(
                    replies_path,
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "script": "eval_agent",
                        "phase": "ingest",
                        "session": session,
                        "step": idx,
                        "step_total": len(items),
                        "turn_label": label,
                        "nanobot_current_time": current_time,
                        "user_message_chars": len(content),
                        "exit_code": cp.returncode,
                        "reply": reply,
                        "stderr_tail": (cp.stderr or "")[-2000:] if cp.stderr else "",
                    },
                )
            if not args.quiet:
                r_prev = reply[:800] + ("…" if len(reply) > 800 else "")
                print(f"[eval_agent] reply_preview:\n{r_prev}\n", file=sys.stderr, flush=True)
            if args.sleep:
                time.sleep(args.sleep)

    nq = len(questions)
    if args.max_questions:
        questions = questions[: args.max_questions]
    use_n = len(questions)
    qa_total = sum(1 for q in questions if (q.get("question") or "").strip())
    print(
        f"[eval_agent] start session={session!r} questions={qa_total} questions_json={q_path}"
        + (f" current_time={qa_current_time}" if qa_current_time else "")
        + (f" replies_log={replies_path}" if replies_path else ""),
        file=sys.stderr,
        flush=True,
    )

    qa_rows: list[dict[str, Any]] = []
    qa_index = 0
    for i, q in enumerate(questions, start=1):
        text = (q.get("question") or "").strip()
        if not text:
            continue
        qa_index += 1
        print(
            f"[eval_agent] {qa_index}/{qa_total} q{i} ({len(text)} chars): {_one_line_preview(text)}",
            file=sys.stderr,
            flush=True,
        )
        if args.dry_run:
            continue
        cp = run_nanobot_agent_message(
            text,
            session_id=session,
            repo_root=repo,
            workspace=args.workspace.resolve() if args.workspace else None,
            config=args.config.resolve() if args.config else None,
            capture_output=True,
            timeout_s=args.timeout,
            current_time=qa_current_time,
        )
        reply_raw = (cp.stdout or "").strip()
        reply = clean_captured_stdout(reply_raw)
        if replies_path is not None:
            _append_reply_jsonl(
                replies_path,
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "script": "eval_agent",
                    "phase": "qa",
                    "session": session,
                    "step": qa_index,
                    "step_total": qa_total,
                    "question_index_in_file": i,
                    "question": text,
                    "nanobot_current_time": qa_current_time,
                    "exit_code": cp.returncode,
                    "reply": reply,
                    "stderr_tail": (cp.stderr or "")[-2000:] if cp.stderr else "",
                },
            )
        if not args.quiet:
            r_prev = reply[:800] + ("…" if len(reply) > 800 else "")
            print(f"[eval_agent] reply_preview:\n{r_prev}\n", file=sys.stderr, flush=True)
        qa_rows.append(
            {
                "question_index_in_file": i,
                "question": text,
                "question_type": q.get("question_type"),
                "ground_truth": q.get("ground_truth"),
                "answer": q.get("answer"),
                "exit_code": cp.returncode,
                "reply": reply,
            }
        )
        if cp.returncode != 0:
            print(f"[eval_agent] WARN q={qa_index} nanobot_exit={cp.returncode} (will score as fail)", file=sys.stderr)
            if cp.stderr:
                sys.stderr.write(cp.stderr)
        if args.sleep:
            time.sleep(args.sleep)

    n_asked = qa_index

    if args.dry_run:
        print(
            f"[eval_agent] done session={session!r} dry-run questions={n_asked}",
            file=sys.stderr,
            flush=True,
        )
        return 0

    passed = 0
    eval_items: list[dict[str, Any]] = []
    print("[eval_agent] score all QA rounds complete", file=sys.stderr, flush=True)
    env_file = bench_dir / ".env"
    if args.llm_judge and not llm_judge_env_ready(env_file):
        print(
            "[eval_agent] ERROR: --llm-judge requires LLM_MODEL_NAME, LLM_API_KEY, "
            "LLM_API_BASE in bench/.env",
            file=sys.stderr,
            flush=True,
        )
        return 1
    use_llm = not args.no_llm_judge and llm_judge_env_ready(env_file)

    if use_llm:
        for row in qa_rows:
            eval_items.append(
                {
                    "question_index_in_file": row["question_index_in_file"],
                    "question": row["question"],
                    "question_type": row["question_type"],
                    "ground_truth": row["ground_truth"],
                    "answer": row["answer"],
                    "exit_code": row["exit_code"],
                    "reply": row["reply"],
                }
            )
        summary_base = {
            "schema": "esl_eval_results_v1",
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": session,
            "ingest_mixed": args.ingest_mixed,
            "mixed_json": str(args.mixed_json.resolve()) if args.ingest_mixed else None,
            "questions_json": str(q_path),
            "nanobot_current_time": qa_current_time,
            "items": eval_items,
        }
        print("[eval_agent] llm_judge scoring (see llm_judge bar on stderr)", file=sys.stderr, flush=True)
        try:
            merged = run_llm_judge(
                summary_base,
                source_eval=str(eval_path) if eval_path else "",
                env_file=env_file,
                no_progress=args.quiet,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[eval_agent] ERROR llm_judge: {e}", file=sys.stderr, flush=True)
            return 1
        passed = int(merged.get("passed") or 0)
        for qa_index, it in enumerate(merged.get("items") or [], start=1):
            st = "PASS" if it.get("pass") else "FAIL"
            src = it.get("pass_source", "?")
            if not it.get("pass"):
                preview = norm_text(it.get("reply") or "")[:120]
                print(
                    f"[eval_agent] q {qa_index}/{n_asked} {st} source={src!r} reply={preview!r}…",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(f"[eval_agent] q {qa_index}/{n_asked} {st} source={src!r}", file=sys.stderr, flush=True)
        print(
            f"[eval_agent] summary pass={passed}/{n_asked} scoring_mode=llm_primary "
            f"(questions_in_slice={use_n}) session={session!r}",
            file=sys.stderr,
            flush=True,
        )
        if eval_path is not None and not args.dry_run:
            eval_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[eval_agent] wrote {eval_path} (schema={merged.get('schema')!r}, scoring_mode=llm_primary)", file=sys.stderr, flush=True)
            lp = merged.get("llm_judge_passed")
            la = merged.get("llm_judge_asked")
            print(
                f"[eval_agent] llm_judge verdicts score==1: {lp}/{la}",
                file=sys.stderr,
                flush=True,
            )
            if args.also_write_eval_results_llm_judge:
                llm_path = result_dir / "eval_results_llm_judge.json"
                llm_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"[eval_agent] also wrote legacy path {llm_path}", file=sys.stderr, flush=True)
    else:
        for qa_index, row in enumerate(qa_rows, start=1):
            item: dict[str, Any] = {
                "question_index_in_file": row["question_index_in_file"],
                "question": row["question"],
                "question_type": row["question_type"],
                "ground_truth": row["ground_truth"],
                "answer": row["answer"],
                "exit_code": row["exit_code"],
                "reply": row["reply"],
            }
            if row["exit_code"] != 0:
                item["pass"] = False
                item["pass_source"] = "nanobot_exit"
                item["score_note"] = "nanobot_nonzero_exit"
                eval_items.append(item)
                print(f"[eval_agent] q {qa_index}/{n_asked} FAIL (exit)", file=sys.stderr, flush=True)
                continue
            ok = score_answer(row["reply"], row["answer"])
            item["pass"] = ok
            item["pass_source"] = "heuristic_only"
            eval_items.append(item)
            if ok:
                passed += 1
                print(f"[eval_agent] q {qa_index}/{n_asked} PASS", file=sys.stderr, flush=True)
            else:
                preview = norm_text(row["reply"])[:120]
                print(
                    f"[eval_agent] q {qa_index}/{n_asked} FAIL answer={row['answer']!r} reply={preview!r}…",
                    file=sys.stderr,
                    flush=True,
                )

        print(
            f"[eval_agent] summary pass={passed}/{n_asked} scoring_mode=heuristic_only "
            f"(questions_in_slice={use_n}) session={session!r}",
            file=sys.stderr,
            flush=True,
        )

        if eval_path is not None and not args.dry_run:
            summary = {
                "schema": "esl_eval_results_v1",
                "ts": datetime.now(timezone.utc).isoformat(),
                "session": session,
                "ingest_mixed": args.ingest_mixed,
                "mixed_json": str(args.mixed_json.resolve()) if args.ingest_mixed else None,
                "questions_json": str(q_path),
                "nanobot_current_time": qa_current_time,
                "scoring_mode": "heuristic_only",
                "passed": passed,
                "asked": n_asked,
                "pass_rate": (passed / n_asked) if n_asked else None,
                "items": eval_items,
            }
            eval_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[eval_agent] wrote {eval_path}", file=sys.stderr, flush=True)

        if not args.no_llm_judge and not llm_judge_env_ready(env_file):
            print(
                "[eval_agent] skip llm_judge (configure LLM_* in bench/.env; use --llm-judge to fail if missing)",
                file=sys.stderr,
                flush=True,
            )

    print(f"[eval_agent] done session={session!r} pass={passed}/{n_asked}", file=sys.stderr, flush=True)
    return 0 if (n_asked == 0 or passed == n_asked) else 1


if __name__ == "__main__":
    raise SystemExit(main())

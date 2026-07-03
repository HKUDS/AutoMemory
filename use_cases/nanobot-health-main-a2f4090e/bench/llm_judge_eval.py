#!/usr/bin/env python3
"""Re-score ``eval_results.json`` items with an LLM judge (OpenAI-compatible API).

Reads API settings from ``bench/.env`` (or ``--env-file``):

- ``LLM_MODEL_NAME`` — model id (e.g. ``qwen/qwen-turbo`` on OpenRouter)
- ``LLM_API_KEY`` — bearer token
- ``LLM_API_BASE`` — base URL (e.g. ``https://openrouter.ai/api/v1``)

Optional in ``.env`` for OpenRouter:

- ``LLM_HTTP_REFERER`` — ``HTTP-Referer`` header (default: this repo path)
- ``LLM_APP_TITLE`` — ``X-Title`` header (default: ``nanobot-health-llm-judge``)

Example::

    cd /path/to/nanobot-health
    uv run bench/llm_judge_eval.py --max-items 5
    uv run bench/llm_judge_eval.py --input bench/run_result/eval_results.json
    uv run bench/llm_judge_eval.py --no-progress   # no tqdm bar
    # Offline format fix (no API); default I/O is eval_results.json in place:
    uv run bench/llm_judge_eval.py --migrate-format-only \\
        --input bench/run_result/eval_results.json \\
        --output bench/run_result/eval_results.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from tqdm import tqdm

from heuristic_score import score_answer

try:
    import json_repair
except ImportError:  # pragma: no cover
    json_repair = None


def _load_env_file(path: Path, *, override: bool) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = val


def _normalize_score(parsed: dict[str, Any]) -> int:
    """Ensure score_0_1 is int 0 or 1."""
    p = bool(parsed.get("pass"))
    raw = parsed.get("score_0_1")
    si: int
    try:
        if isinstance(raw, bool):
            si = int(raw)
        elif raw is None:
            si = 1 if p else 0
        elif isinstance(raw, (int, float)):
            fv = float(raw)
            if fv >= 0.5:
                si = 1
            else:
                si = 0
        else:
            si = int(raw)
            if si not in (0, 1):
                si = 1 if si >= 1 else 0
    except (TypeError, ValueError):
        si = 1 if p else 0
    if si not in (0, 1):
        si = 1 if si >= 1 else 0
    parsed["score_0_1"] = si
    parsed.pop("pass", None)
    return si


def _parse_judge_json(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}\s*$", text)
    if m:
        text = m.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if json_repair is not None:
        repaired = json_repair.loads(text)
        if isinstance(repaired, dict):
            return repaired
    raise ValueError(f"Could not parse judge JSON from model output: {text[:500]!r}")


def _judge_one(
    client: httpx.Client,
    *,
    api_base: str,
    model: str,
    question: str,
    reference_answer: Any,
    assistant_reply: str,
    ground_truth: Any,
    temperature: float,
) -> dict[str, Any]:
    base = api_base.rstrip("/")
    url = f"{base}/chat/completions"
    system = (
        "You are an impartial evaluator for a health QA benchmark. "
        "Decide whether the assistant's reply correctly answers the question "
        "with respect to the reference answer in a normal daily conversation. "
        "The reference answer comes from the benchmark key; treat it as the gold target, "
        "but do not require excessive decimal precision. "
        "Pass reasonable rounded or unit-equivalent numeric answers when they clearly "
        "refer to the same metric and date, such as 35.9616 hours answered as 36 hours, "
        "49.0263 answered as 49.0, or 398.7064 minutes answered as about 399 minutes. "
        "Do not require trailing zeros or exact wording. "
        "If the reference answer is null, pass only if the assistant correctly states "
        "the value is missing, not recorded, or cannot be determined from context. "
        "Do not pass if the assistant substitutes a different metric, gives only a vague "
        "range instead of the requested value, guesses, or says the value was not recorded "
        "when the reference answer is present. "
        "Respond with a single JSON object only, no markdown fences, keys: "
        "score_0_1 (integer 0 or 1), reasoning (short string)."
    )
    user = json.dumps(
        {
            "question": question,
            "reference_answer": reference_answer,
            "ground_truth_paths": ground_truth,
            "assistant_reply": assistant_reply,
        },
        ensure_ascii=False,
    )
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    r = client.post(url, json=payload)
    r.raise_for_status()
    body = r.json()
    choice = (body.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    parsed = _parse_judge_json(content)
    if "score_0_1" not in parsed and "pass" not in parsed:
        raise ValueError(f"Judge JSON missing 'score_0_1': {parsed!r}")
    if "reasoning" not in parsed:
        parsed["reasoning"] = ""
    _normalize_score(parsed)
    return parsed


def llm_judge_env_ready(env_file: Path | None = None, *, env_override: bool = False) -> bool:
    """Return True if ``LLM_MODEL_NAME``, ``LLM_API_KEY``, and ``LLM_API_BASE`` are set after loading ``env_file``."""
    bench_dir = Path(__file__).resolve().parent
    path = (env_file or (bench_dir / ".env")).resolve()
    _load_env_file(path, override=env_override)
    return all(os.environ.get(k, "").strip() for k in ("LLM_MODEL_NAME", "LLM_API_KEY", "LLM_API_BASE"))


def run_llm_judge(
    data: dict[str, Any],
    *,
    source_eval: str,
    env_file: Path | None = None,
    env_override: bool = False,
    max_items: int = 0,
    sleep_s: float = 0.35,
    temperature: float = 0.0,
    no_progress: bool = False,
) -> dict[str, Any]:
    """Run LLM judge on ``eval_results``-shaped ``data``; return merged summary (not written to disk).

    Expects ``LLM_MODEL_NAME``, ``LLM_API_KEY``, ``LLM_API_BASE`` in environment after loading ``env_file``.
    ``source_eval`` is stored as ``llm_judge_source_eval`` (path to the companion ``eval_results.json``).

    **Scoring**: Each item's ``pass`` follows the LLM ``score_0_1`` when the API call succeeds.
    If the judge call raises (network, parse, etc.), ``pass`` falls back to :func:`heuristic_score.score_answer`.
    Nanobot nonzero ``exit_code`` items skip the API and get ``pass`` false.

    Top-level ``passed`` / ``pass_rate`` are recomputed from final ``pass`` on every item.
    ``llm_judge_passed`` / ``llm_judge_asked`` count only successful LLM verdicts (``exit_code`` 0
    and no judge exception); ``llm_judge_passed`` increments only when ``score_0_1 == 1``.
    """
    bench_dir = Path(__file__).resolve().parent
    env_path = (env_file or (bench_dir / ".env")).resolve()
    _load_env_file(env_path, override=env_override)

    model = os.environ.get("LLM_MODEL_NAME", "").strip()
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    api_base = os.environ.get("LLM_API_BASE", "").strip()
    for name, val in (
        ("LLM_MODEL_NAME", model),
        ("LLM_API_KEY", api_key),
        ("LLM_API_BASE", api_base),
    ):
        if not val:
            raise RuntimeError(f"missing {name} in environment or {env_path}")

    items: list[dict[str, Any]] = data.get("items") or []
    if not isinstance(items, list):
        raise ValueError("eval data missing items[]")

    referer = os.environ.get("LLM_HTTP_REFERER", "https://github.com/nanobot-ai/nanobot-health").strip()
    title = os.environ.get("LLM_APP_TITLE", "nanobot-health-llm-judge").strip()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": referer,
        "X-Title": title,
    }

    to_judge = list(items)
    if max_items:
        to_judge = to_judge[:max_items]

    out_items: list[dict[str, Any]] = []
    llm_passed = 0
    llm_asked = 0

    bar = tqdm(
        to_judge,
        desc="llm_judge",
        unit="item",
        disable=no_progress,
        file=sys.stderr,
        dynamic_ncols=True,
    )
    with httpx.Client(timeout=120.0, headers=headers) as client:
        for it in bar:
            q = it.get("question") or ""
            ref = it.get("answer")
            gt = it.get("ground_truth")
            reply = it.get("reply") or ""
            ec = it.get("exit_code", 0)
            qidx = it.get("question_index_in_file")
            bar.set_postfix_str(f"q={qidx}", refresh=False)
            row = dict(it)
            if ec != 0:
                row["pass"] = False
                row["pass_source"] = "nanobot_exit"
                row["llm_judge"] = {
                    "score_0_1": 0,
                    "reasoning": "skipped: nanobot nonzero exit_code",
                    "model": model,
                }
            else:
                llm_asked += 1
                try:
                    judge = _judge_one(
                        client,
                        api_base=api_base,
                        model=model,
                        question=q,
                        reference_answer=ref,
                        assistant_reply=reply,
                        ground_truth=gt,
                        temperature=temperature,
                    )
                except Exception as e:  # noqa: BLE001
                    fb = score_answer(reply, ref)
                    row["pass"] = fb
                    row["pass_source"] = "heuristic_fallback"
                    row["llm_judge"] = {
                        "score_0_1": None,
                        "reasoning": f"judge_error: {e}",
                        "model": model,
                        "heuristic_fallback_pass": fb,
                    }
                else:
                    judge["model"] = model
                    row["llm_judge"] = judge
                    row["pass"] = judge.get("score_0_1") == 1
                    row["pass_source"] = "llm_judge"
                    if judge.get("score_0_1") == 1:
                        llm_passed += 1
            bar.set_postfix_str(f"q={qidx} ok={llm_passed}/{llm_asked}", refresh=True)
            out_items.append(row)
            if sleep_s:
                time.sleep(sleep_s)

    if max_items and len(out_items) < len(items):
        for tail in items[len(out_items) :]:
            row = dict(tail)
            ec = row.get("exit_code", 0)
            reply = row.get("reply") or ""
            ref = row.get("answer")
            if ec != 0:
                row["pass"] = False
                row["pass_source"] = "nanobot_exit"
                row["llm_judge"] = {
                    "score_0_1": 0,
                    "reasoning": "skipped: nanobot nonzero exit_code (not in llm_judge batch)",
                    "model": model,
                }
            else:
                h = score_answer(reply, ref)
                row["pass"] = h
                row["pass_source"] = "heuristic_only"
                row["llm_judge"] = {
                    "score_0_1": None,
                    "reasoning": "skipped: beyond --max-items; heuristic pass only",
                    "model": model,
                    "heuristic_pass": h,
                }
            out_items.append(row)

    total_asked = len(out_items)
    total_passed = sum(1 for r in out_items if r.get("pass"))

    summary = dict(data)
    summary["schema"] = "esl_eval_results_llm_judge_v1"
    summary["llm_judge_source_eval"] = source_eval
    summary["llm_judge_model"] = model
    summary["llm_judge_api_base"] = api_base
    summary["llm_judge_passed"] = llm_passed
    summary["llm_judge_asked"] = llm_asked
    summary["llm_judge_pass_rate"] = (llm_passed / llm_asked) if llm_asked else None
    summary["passed"] = total_passed
    summary["asked"] = total_asked
    summary["pass_rate"] = (total_passed / total_asked) if total_asked else None
    summary["scoring_mode"] = "llm_primary"
    summary["items"] = out_items
    return summary


def _migrate_one_item_to_current_format(row: dict[str, Any]) -> dict[str, Any]:
    """Recompute ``pass`` / ``pass_source`` / ``llm_judge`` from existing fields (no API)."""
    row = dict(row)
    ec = int(row.get("exit_code") or 0)
    reply = row.get("reply") or ""
    ans = row.get("answer")
    lj_in = row.get("llm_judge")
    lj: dict[str, Any] = dict(lj_in) if isinstance(lj_in, dict) else {}
    reasoning = str(lj.get("reasoning") or "")
    model = str(lj.get("model") or "")

    if ec != 0:
        row["pass"] = False
        row["pass_source"] = "nanobot_exit"
        if "skipped:" not in reasoning.lower():
            row["llm_judge"] = {
                "score_0_1": 0,
                "reasoning": "skipped: nanobot nonzero exit_code",
                "model": model,
            }
        else:
            row["llm_judge"] = {**lj, "score_0_1": 0, "model": model or lj.get("model", "")}
        return row

    if "judge_error:" in reasoning:
        fb = score_answer(reply, ans)
        row["pass"] = fb
        row["pass_source"] = "heuristic_fallback"
        row["llm_judge"] = {
            "score_0_1": None,
            "reasoning": reasoning,
            "model": model,
            "heuristic_fallback_pass": fb,
        }
        return row

    if lj.get("heuristic_fallback_pass") is not None and lj.get("score_0_1") is None:
        row["pass"] = bool(lj["heuristic_fallback_pass"])
        row["pass_source"] = "heuristic_fallback"
        row["llm_judge"] = {**lj, "model": model or lj.get("model", "")}
        return row

    if "beyond --max-items" in reasoning or "heuristic pass only" in reasoning:
        h = lj.get("heuristic_pass")
        if h is None:
            h = score_answer(reply, ans)
        row["pass"] = bool(h)
        row["pass_source"] = "heuristic_only"
        row["llm_judge"] = {
            "score_0_1": None,
            "reasoning": reasoning or "skipped: beyond --max-items; heuristic pass only",
            "model": model,
            "heuristic_pass": h,
        }
        return row

    raw = lj.get("score_0_1")
    if raw is None:
        fb = score_answer(reply, ans)
        row["pass"] = fb
        row["pass_source"] = "heuristic_fallback"
        row["llm_judge"] = {
            **lj,
            "score_0_1": None,
            "model": model,
            "reasoning": reasoning or "migrated: missing score_0_1; heuristic fallback",
            "heuristic_fallback_pass": fb,
        }
        return row

    try:
        si = 1 if float(raw) >= 0.5 else 0
    except (TypeError, ValueError):
        si = 1 if raw == 1 else 0
    row["pass"] = si == 1
    row["pass_source"] = "llm_judge"
    row["llm_judge"] = {**lj, "score_0_1": si, "model": model or lj.get("model", "")}
    return row


def rewrite_llm_judge_eval_local(data: dict[str, Any]) -> dict[str, Any]:
    """Align an existing LLM eval JSON (e.g. ``eval_results.json``) to the current schema without calling the API.

    - Sets per-item ``pass`` / ``pass_source`` from stored ``llm_judge`` (LLM verdict, or heuristic
      when the record was a ``judge_error``).
    - Recomputes top-level ``passed`` / ``asked`` / ``pass_rate`` and ``llm_judge_*`` counters.
    """
    items_in = data.get("items")
    if not isinstance(items_in, list):
        raise ValueError("data missing items[]")
    out_items = [_migrate_one_item_to_current_format(dict(it)) for it in items_in]

    llm_passed = 0
    llm_asked = 0
    for r in out_items:
        if int(r.get("exit_code") or 0) != 0:
            continue
        ps = r.get("pass_source")
        lj = r.get("llm_judge") or {}
        rs = str(lj.get("reasoning") or "")
        if ps == "llm_judge":
            llm_asked += 1
            if lj.get("score_0_1") == 1:
                llm_passed += 1
        elif ps == "heuristic_fallback" and "judge_error:" in rs:
            llm_asked += 1

    total_asked = len(out_items)
    total_passed = sum(1 for r in out_items if r.get("pass"))

    summary = dict(data)
    summary["schema"] = "esl_eval_results_llm_judge_v1"
    summary["scoring_mode"] = "llm_primary"
    summary["llm_judge_passed"] = llm_passed
    summary["llm_judge_asked"] = llm_asked
    summary["llm_judge_pass_rate"] = (llm_passed / llm_asked) if llm_asked else None
    summary["passed"] = total_passed
    summary["asked"] = total_asked
    summary["pass_rate"] = (total_passed / total_asked) if total_asked else None
    summary["items"] = out_items
    return summary


def eval_results_body_without_judge_details(merged: dict[str, Any]) -> dict[str, Any]:
    """Strip per-item ``llm_judge`` and LLM-only top-level keys for ``eval_results.json``."""
    drop_top = {
        "llm_judge_source_eval",
        "llm_judge_model",
        "llm_judge_api_base",
        "llm_judge_passed",
        "llm_judge_asked",
        "llm_judge_pass_rate",
    }
    body: dict[str, Any] = {k: v for k, v in merged.items() if k not in drop_top and k != "items"}
    body["schema"] = "esl_eval_results_v1"
    body["items"] = [
        {k: v for k, v in (it if isinstance(it, dict) else {}).items() if k != "llm_judge"}
        for it in (merged.get("items") or [])
    ]
    return body


def main() -> int:
    bench_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=bench_dir / ".env")
    parser.add_argument(
        "--input",
        type=Path,
        default=bench_dir / "run_result" / "eval_results.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="default: same as --input",
    )
    parser.add_argument("--max-items", type=int, default=0, help="0 = judge all items")
    parser.add_argument("--sleep", type=float, default=0.35, help="Seconds between API calls")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--env-override",
        action="store_true",
        help="Values from --env-file override existing environment variables",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned count only")
    parser.add_argument(
        "--migrate-format-only",
        action="store_true",
        help=(
            "Re-read --input, recompute pass/pass_source and top-level totals from stored "
            "llm_judge rows only; no API calls. Writes --output (default: same file as --input)."
        ),
    )
    parser.add_argument(
        "--also-sync-eval-results",
        action="store_true",
        help=(
            "With --migrate-format-only, also write a compact JSON without per-item llm_judge: "
            "eval_results.json in the same directory if --output is not eval_results.json; "
            "otherwise eval_results_compact.json."
        ),
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable tqdm progress bar (still logs final summary)",
    )
    args = parser.parse_args()

    inp = args.input.resolve()
    out_path = (args.output or args.input).resolve()
    if not inp.is_file():
        print(f"[llm_judge] ERROR: input not found: {inp}", file=sys.stderr)
        return 1

    data = json.loads(inp.read_text(encoding="utf-8"))

    if args.migrate_format_only:
        try:
            summary = rewrite_llm_judge_eval_local(data)
        except ValueError as e:
            print(f"[llm_judge] ERROR migrate: {e}", file=sys.stderr)
            return 1
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tp = int(summary.get("passed") or 0)
        ta = int(summary.get("asked") or 0)
        lp = int(summary.get("llm_judge_passed") or 0)
        la = int(summary.get("llm_judge_asked") or 0)
        print(
            f"[llm_judge] migrate-format: wrote {out_path} pass={tp}/{ta} (llm_score1={lp}/{la})",
            file=sys.stderr,
            flush=True,
        )
        if args.also_sync_eval_results:
            er_body = eval_results_body_without_judge_details(summary)
            if out_path.name == "eval_results.json":
                er_path = out_path.parent / "eval_results_compact.json"
            else:
                er_path = out_path.parent / "eval_results.json"
            er_path.write_text(json.dumps(er_body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[llm_judge] migrate-format: wrote compact {er_path}", file=sys.stderr, flush=True)
        return 0

    items: list[dict[str, Any]] = data.get("items") or []
    if not isinstance(items, list):
        print("[llm_judge] ERROR: eval_results.json missing items[]", file=sys.stderr)
        return 1

    to_judge = list(items)
    if args.max_items:
        to_judge = to_judge[: args.max_items]

    if args.dry_run:
        print(f"[llm_judge] dry-run would judge {len(to_judge)} item(s) (total items={len(items)})", file=sys.stderr)
        return 0

    try:
        summary = run_llm_judge(
            data,
            source_eval=str(inp),
            env_file=args.env_file.resolve(),
            env_override=args.env_override,
            max_items=args.max_items,
            sleep_s=args.sleep,
            temperature=args.temperature,
            no_progress=args.no_progress,
        )
    except RuntimeError as e:
        print(f"[llm_judge] ERROR: {e}", file=sys.stderr)
        return 1

    llm_passed = int(summary.get("llm_judge_passed") or 0)
    llm_asked = int(summary.get("llm_judge_asked") or 0)
    tot_passed = int(summary.get("passed") or 0)
    tot_asked = int(summary.get("asked") or 0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[llm_judge] wrote {out_path} pass={tot_passed}/{tot_asked} "
        f"(llm_score1={llm_passed}/{llm_asked})",
        file=sys.stderr,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

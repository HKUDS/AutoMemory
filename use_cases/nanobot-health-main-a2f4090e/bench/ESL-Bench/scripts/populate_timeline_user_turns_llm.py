#!/usr/bin/env python3
"""Populate daily natural-language user turns from timeline JSON via LLM.

Per calendar day:
- The model sees all sparse timeline rows for that day.
- The model writes one or more natural first-person user sends, as if the user typed them
  at different moments in the day (exact count is not enforced in code).
- If ``question_sparse40.json`` references any entry for that day, those entries
  are passed as **required QA facts** and must be explicitly mentioned in the
  generated messages. Other same-day rows should be used as realistic noise.

Reads API settings from ``bench/.env`` by default::

    LLM_API_KEY=...
    LLM_API_BASE=https://openrouter.ai/api/v1
    LLM_MODEL_NAME=...

Uses the OpenAI-compatible ``/chat/completions`` endpoint. Requires: ``pip install openai``.
Optional: ``pip install tqdm`` for a progress bar.

Example::

    python3 populate_timeline_user_turns_llm.py --dry-run
    python3 populate_timeline_user_turns_llm.py --output ../data/construction(2026-04)/user110_AT_demo/user_turn_timeline_daily_llm.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

_BENCH = Path(__file__).resolve().parents[1]
_DEFAULT_CONSTRUCTION = _BENCH / "data" / "construction(2026-04)" / "user110_AT_demo"
_DEFAULT_EVALUATION = _BENCH / "data" / "evaluation"

try:
    from openai import OpenAI
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: openai. Install with: pip install openai\n" + str(e)
    ) from e

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore[misc, assignment]


def _log(msg: str) -> None:
    if tqdm is not None:
        tqdm.write(msg)
    else:
        print(msg)


def _load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


DEVICE_NICE: dict[str, str] = {
    "smart_scale": "smart scale",
    "smartwatch": "smart watch",
    "bp_monitor": "BP monitor",
    "cgm": "CGM",
    "thermometer": "thermometer",
}


def _pretty_indicator_name(indicator: str) -> str:
    """Turn e.g. BodyMassIndex-BMI into 'Body Mass Index'; keep 24-Hour... prefixes."""
    if not indicator:
        return "?"
    base = indicator
    if "-" in indicator:
        left, right = indicator.rsplit("-", 1)
        if len(right) <= 6 and right.isalpha() and right.isupper():
            base = left
        else:
            base = indicator.replace("-", " ")
    s = base.replace("-", " ")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([0-9])([A-Za-z])", r"\1 \2", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or indicator


def _fmt_scalar(v: Any) -> str:
    if v is None:
        return "(no reading)"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "(no reading)"
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        s = f"{v:.8f}".rstrip("0").rstrip(".")
        return s or "0"
    return str(v)


def _compact_timeline_entries(entries: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, e in entries:
        row: dict[str, Any] = {
            "i": idx,
            "time": e.get("time"),
            "entry_type": e.get("entry_type"),
        }
        if e.get("entry_type") == "device_indicator":
            val = _fmt_scalar(e.get("value"))
            unit = (e.get("unit") or "").strip()
            row.update(
                {
                    "indicator": e.get("indicator"),
                    "label": _pretty_indicator_name(e.get("indicator") or "?"),
                    "value": val,
                    "unit": unit,
                    "device": DEVICE_NICE.get(e.get("device_type") or "", e.get("device_type") or "device"),
                }
            )
            if val != "(no reading)" and unit:
                row["spoken_value"] = f"{val} {unit}"
            else:
                row["spoken_value"] = val
        elif e.get("entry_type") == "event":
            row.update(
                {
                    "end_time": e.get("end_time"),
                    "event": e.get("event"),
                }
            )
        else:
            row["raw"] = e
        out.append(row)
    return out


def _question_requirements(
    question_path: Path,
    *,
    source_name: str,
    entries: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    if not question_path.is_file():
        return {}

    questions = json.loads(question_path.read_text(encoding="utf-8"))
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for qi, item in enumerate(questions):
        for gt in item.get("ground_truth", []):
            if not isinstance(gt, str):
                continue
            m = re.search(r"([^/\\]+\.json)\.entries\[(\d+)\]", gt)
            if not m or m.group(1) != source_name:
                continue
            idx = int(m.group(2))
            if idx < 0 or idx >= len(entries):
                continue
            day = str(entries[idx].get("time") or "")[:10]
            if len(day) != 10:
                continue
            by_day[day].append(
                {
                    "question_index": qi,
                    "question": item.get("question"),
                    "answer": item.get("answer"),
                    "ground_truth": gt,
                    "entry": _compact_timeline_entries([(idx, entries[idx])])[0],
                }
            )
    return dict(by_day)


SYSTEM_PROMPT = """You write natural **user** chat messages for **nanobot**, a personal AI assistant.
You will receive JSON describing one calendar day's sparse health timeline.
Write as the user in first person, casual English, plain text.

Critical requirements:
- Return only valid JSON: {"messages": ["...", "..."]}.
- Produce one or more messages. Imagine they were typed at different moments of that day.
- Every required QA fact must be explicitly included, with the exact numeric value and unit when provided.
- Also include realistic noise from other same-day entries, so the messages are not a direct answer dump.
- Stay faithful to the provided facts. Do not invent diagnoses, symptoms, devices, dates, or values.
- Do not mention "AutoSender", "ground truth", "benchmark", "JSON", or entry indices."""


USER_PROMPT_TIMELINE = """Date: **{date}**

Below is one day of sparse timeline data. Generate natural user input for nanobot.

The user messages MUST include the answer to every item in "required_qa_facts".
Use "same_day_entries" for extra context/noise. Never directly expose required_qa_fact as one message.
You should cover as much information in the same day as possible, but keeps the natural language flow.

Natural style examples:
- "Morning check-in: my weight was 78.1 kg today, and my sleep felt pretty decent after about 7 hours."
- "After my workout, my recovery time showed 35.9616 hours. I also noticed my training readiness was on the low side."
- "Quick log for tonight: stress was moderate, and my glucose readings looked pretty normal."

---
required_qa_facts:
{required_json}

same_day_entries:
{entries_json}
"""


def _strip_model_fences(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:\w+)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip().strip('"').strip("'")


def _extract_messages_from_model(text: str) -> list[str]:
    cleaned = _strip_model_fences(text)
    obj = json.loads(cleaned)
    raw_messages: Any
    if isinstance(obj, dict):
        raw_messages = obj.get("messages")
    else:
        raw_messages = obj
    if not isinstance(raw_messages, list):
        raise ValueError("model response must contain a messages list")
    messages = [m.strip() for m in raw_messages if isinstance(m, str) and m.strip()]
    if not messages:
        raise ValueError("model response must contain at least one non-empty string in messages")
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=_DEFAULT_CONSTRUCTION / "timeline_26_filtered_sparse40.json",
        help="Sparse timeline JSON used to generate daily natural user turns",
    )
    parser.add_argument(
        "--question",
        type=Path,
        default=_DEFAULT_EVALUATION / "question_sparse40.json",
        help="Question file whose timeline ground-truth entries must be mentioned",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_CONSTRUCTION / "user_turn_timeline_daily_llm.json",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[2] / ".env",
        help="Path to .env with LLM_API_KEY, LLM_API_BASE, LLM_MODEL_NAME",
    )
    parser.add_argument("--model", type=str, default=None, help="Override LLM_MODEL_NAME from .env")
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--max-tokens", type=int, default=900)
    parser.add_argument("--sleep", type=float, default=0.4, help="Seconds between API calls")
    parser.add_argument("--dry-run", action="store_true", help="Print first-day prompt and exit (no API)")
    parser.add_argument("--max-days", type=int, default=0, help="Process at most N days (0 = all)")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="If output exists, skip days already present (by date key)",
    )
    args = parser.parse_args()

    env = _load_dotenv(args.env_file)
    api_key = os.environ.get("LLM_API_KEY") or env.get("LLM_API_KEY")
    api_base = os.environ.get("LLM_API_BASE") or env.get("LLM_API_BASE")
    model = args.model or os.environ.get("LLM_MODEL_NAME") or env.get("LLM_MODEL_NAME")

    if not args.dry_run and (not api_key or not api_base or not model):
        print(
            "Missing LLM_API_KEY / LLM_API_BASE / LLM_MODEL_NAME. "
            f"Set them in {args.env_file} or export in the environment.",
            file=sys.stderr,
        )
        return 1

    data = json.loads(args.input.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = data["entries"]
    by_day: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for i, e in enumerate(entries):
        t = e.get("time") or ""
        day = t[:10]
        if len(day) == 10:
            by_day[day].append((i, e))

    required_by_day = _question_requirements(
        args.question,
        source_name=args.input.name,
        entries=entries,
    )

    days_sorted = sorted(by_day.keys())
    if args.max_days:
        days_sorted = days_sorted[: args.max_days]

    existing_dates: set[str] = set()
    out_doc: dict[str, Any]
    if args.resume and args.output.is_file():
        try:
            out_doc = json.loads(args.output.read_text(encoding="utf-8"))
            for t in out_doc.get("turns", []):
                if isinstance(t.get("date"), str):
                    existing_dates.add(t["date"])
        except json.JSONDecodeError:
            out_doc = {
                "user_id": data.get("user_id"),
                "source_file": args.input.name,
                "question_file": args.question.name,
                "aggregation": "daily_llm_natural_variable_messages_required_question_facts_plus_noise",
                "model": model,
                "turns": [],
            }
    else:
        out_doc = {
            "user_id": data.get("user_id"),
            "source_file": args.input.name,
            "question_file": args.question.name,
            "aggregation": "daily_llm_natural_variable_messages_required_question_facts_plus_noise",
            "model": model,
            "turns": [],
        }

    if args.dry_run:
        # Prefer a day with required QA facts so the alignment constraint is visible.
        d0 = next((d for d in days_sorted if required_by_day.get(d)), days_sorted[0])
        day_entries = by_day[d0]
        compact_entries = _compact_timeline_entries(day_entries)
        required = required_by_day.get(d0, [])
        user_msg = USER_PROMPT_TIMELINE.format(
            date=d0,
            required_json=json.dumps(required, ensure_ascii=False, indent=2),
            entries_json=json.dumps(compact_entries, ensure_ascii=False, indent=2),
        )
        print("=== DRY RUN: first day ===", d0)
        print("--- LLM user prompt (timeline + required QA facts) ---\n", user_msg[:5000], ("\n... [truncated]" if len(user_msg) > 5000 else ""))
        print("\nrows that day:", len(day_entries), "required QA facts:", len(required))
        print("required QA days:", len(required_by_day), "question file:", args.question)
        return 0

    client = OpenAI(api_key=api_key, base_url=api_base.rstrip("/"))

    day_iter = (
        tqdm(days_sorted, desc="Daily LLM user turns", unit="day")
        if tqdm is not None
        else days_sorted
    )
    for day in day_iter:
        if day in existing_dates:
            _log(f"skip (resume): {day}")
            continue
        day_entries = by_day[day]
        compact_entries = _compact_timeline_entries(day_entries)
        required = required_by_day.get(day, [])
        user_content = USER_PROMPT_TIMELINE.format(
            date=day,
            required_json=json.dumps(required, ensure_ascii=False, indent=2),
            entries_json=json.dumps(compact_entries, ensure_ascii=False, indent=2),
        )
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
            )
            choice = resp.choices[0].message.content
            if not choice:
                raise RuntimeError("empty model response")
            natural_messages = _extract_messages_from_model(choice)
        except Exception as ex:  # pragma: no cover
            _log(f"ERROR {day}: {ex}")
            return 1

        msgs: list[dict[str, str]] = [{"role": "user", "content": msg} for msg in natural_messages]
        total_chars = sum(len(m["content"]) for m in msgs)

        indices = [i for i, _ in day_entries]
        out_doc["turns"].append(
            {
                "date": day,
                "source_entry_indices": indices,
                "required_question_entry_indices": [r["entry"]["i"] for r in required],
                "source_row_count": len(day_entries),
                "messages": msgs,
            }
        )
        out_doc["model"] = model
        out_doc["question_file"] = args.question.name
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(out_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if tqdm is not None and hasattr(day_iter, "set_postfix"):
            day_iter.set_postfix_str(
                f"{total_chars}c, {len(msgs)}msg, required={len(required)}", refresh=False
            )
        _log(f"done: {day} -> {len(msgs)} user msg(s), {total_chars} chars, required={len(required)}")
        time.sleep(args.sleep)

    _log(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

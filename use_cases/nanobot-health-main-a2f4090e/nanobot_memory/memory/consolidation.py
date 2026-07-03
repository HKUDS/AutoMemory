"""
Core health memory consolidation: LLM-driven extraction of health facts
from conversation transcripts into HEALTH.md and health_log.jsonl.

Reuses the nanobot agent's OpenRouter API via litellm.
"""

import json
import os
import re
from datetime import datetime

import litellm

from .prompts import HEALTH_EXTRACT_SYSTEM, HEALTH_EXTRACT_USER

litellm.suppress_debug_info = True

DEFAULT_MODEL = "openrouter/minimax/minimax-m2.7"

HEALTH_TEMPLATE = """\
# Health Profile

## Basic Info
- Date of birth:
- Blood type:
- Emergency contact:

## Problem List
(No entries yet)

## Medications
(No entries yet)

## Allergies & Adverse Reactions
(No entries yet)

## Recent Care Timeline
(No entries yet)

## Goals & Care Plan
(No entries yet)
"""


def _parse_response(text):
    """Extract HEALTH_PROFILE and HEALTH_LOG blocks from the LLM response."""
    profile_match = re.search(
        r"<HEALTH_PROFILE>\s*(.*?)\s*</HEALTH_PROFILE>", text, re.DOTALL
    )
    log_match = re.search(
        r"<HEALTH_LOG>\s*(.*?)\s*</HEALTH_LOG>", text, re.DOTALL
    )

    profile = profile_match.group(1).strip() if profile_match else None
    log_text = log_match.group(1).strip() if log_match else None

    return profile, log_text


def _add_human_date(entry):
    """Add a human-readable date field to a log entry for natural-language search."""
    ts = entry.get("timestamp", "")
    if ts:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(ts[:16] if len(ts) > 10 else ts, fmt)
                entry["date_human"] = dt.strftime("%B %-d, %Y")
                break
            except ValueError:
                continue
    return entry


def _parse_log_entries(log_text):
    """Parse JSONL lines from the health log block."""
    entries = []
    if not log_text:
        return entries
    for line in log_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if isinstance(entry, dict) and "category" in entry:
                if "timestamp" not in entry:
                    entry["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                _add_human_date(entry)
                entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries


def format_nanobot_transcript(messages):
    """Format nanobot-style messages into a readable transcript."""
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if not content or content is None:
            continue

        if isinstance(content, list):
            text_parts = [
                p.get("text", "") for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            content = "\n".join(text_parts)

        if not content.strip():
            continue

        if role == "user":
            lines.append(f"User: {content}")
        elif role == "assistant":
            lines.append(f"Assistant: {content}")
        elif role == "tool":
            tool_name = msg.get("name", "tool")
            lines.append(f"Tool ({tool_name}): {content[:500]}")

    return "\n\n".join(lines)


def consolidate(messages, current_health="", model=None, api_key=None, api_base=None):
    """
    Run a single LLM consolidation call to extract health data.

    Args:
        messages: List of nanobot-format message dicts.
        current_health: Current contents of HEALTH.md.
        model: LiteLLM model string.
        api_key: Optional API key override.
        api_base: Optional API base URL override.

    Returns:
        (updated_health, log_entries) where:
        - updated_health is the new HEALTH.md content (str)
        - log_entries is a list of dicts for health_log.jsonl
    """
    model = model or os.environ.get("NANOBOT_MEMORY_MODEL", DEFAULT_MODEL)

    transcript = format_nanobot_transcript(messages)
    if not transcript.strip():
        return current_health, []

    health_display = current_health if current_health.strip() else HEALTH_TEMPLATE

    user_prompt = HEALTH_EXTRACT_USER.format(
        health_content=health_display,
        transcript=transcript,
    )

    params = {
        "model": model,
        "messages": [
            {"role": "system", "content": HEALTH_EXTRACT_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "stream": False,
    }
    if api_key:
        params["api_key"] = api_key
    if api_base:
        params["api_base"] = api_base

    response = litellm.completion(**params)
    text = response.choices[0].message.content

    updated_health, log_text = _parse_response(text)

    if updated_health is None:
        updated_health = current_health

    log_entries = _parse_log_entries(log_text)

    return updated_health, log_entries


def read_file(path):
    """Read a file, returning empty string if it doesn't exist."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def write_file(path, content):
    """Write content to a file, creating parent dirs if needed."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def append_health_log(path, entries):
    """Append JSONL entries to health_log.jsonl."""
    if not entries:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

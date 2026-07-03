"""
Refine adapter for nanobot health memory system.

Provides the standard init/memorize/recall interface for trace replay.
This adapter wraps the health memory consolidation logic behind a uniform API.

Write granularity: agent-triggered, per-turn.
The runner calls memorize() per turn since the production agent writes
health data immediately during conversation.
"""

import json
import os
import re
from calendar import month_name as _MONTH_NAMES
from datetime import datetime

from .consolidation import (
    HEALTH_TEMPLATE,
    append_health_log,
    consolidate,
    read_file,
    write_file,
)

_data_dir = None
_health_path = None
_health_log_path = None

_MONTH_MAP = {name.lower(): i for i, name in enumerate(_MONTH_NAMES) if name}
_MONTH_PAT = re.compile(
    r"\b(january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+(\d{1,2})(?:,?\s*(\d{4}))?\b",
    re.IGNORECASE,
)
# Suffixes stripped for morphological prefix matching (order matters — longest first)
_MORPH_SUFFIXES = (
    "ings", "tion", "tions", "ness", "ment", "ments", "ing", "tion",
    "ed", "er", "ers", "est", "en", "ly", "al",
)


def _iso_from_natural_date(query):
    """Return an ISO date prefix for 'April 15' / 'April 15, 2026' queries, or None."""
    m = _MONTH_PAT.search(query)
    if not m:
        return None
    month_str, day_str, year_str = m.group(1), m.group(2), m.group(3)
    month_num = _MONTH_MAP[month_str.lower()]
    year = int(year_str) if year_str else datetime.now().year
    return f"{year}-{month_num:02d}-{int(day_str):02d}"


def _morph_root(word):
    """Strip common English inflectional/derivational suffixes to get a search root."""
    w = word.lower()
    for suffix in _MORPH_SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            return w[: -len(suffix)]
    # Strip trailing -s only when root would be >= 4 chars and not end in -ss
    if w.endswith("s") and not w.endswith("ss") and len(w) >= 5:
        return w[:-1]
    return w


def _morph_terms(query):
    """Return morphological root forms for each word in the query (>= 4 chars)."""
    roots = set()
    for word in re.split(r"\W+", query):
        if len(word) >= 4:
            root = _morph_root(word)
            if root != word.lower():
                roots.add(root)
    return roots


def init(data_dir):
    """
    Initialize the memory system with an isolated data directory.

    Args:
        data_dir: Path to a directory for storing HEALTH.md and health_log.jsonl.
                  Created if it doesn't exist.
    """
    global _data_dir, _health_path, _health_log_path
    _data_dir = data_dir
    os.makedirs(data_dir, exist_ok=True)
    _health_path = os.path.join(data_dir, "HEALTH.md")
    _health_log_path = os.path.join(data_dir, "health_log.jsonl")

    if not os.path.exists(_health_path):
        write_file(_health_path, HEALTH_TEMPLATE)


def memorize(context):
    """
    Push conversation content through the health memory extraction.

    Args:
        context: Either a list of message dicts, or a dict with:
            - "messages": list of message dicts (required)
            - "model": LiteLLM model string (optional)
            - "api_key": API key (optional)
            - "api_base": API base URL (optional)
    """
    if _data_dir is None:
        raise RuntimeError("Memory not initialized. Call init(data_dir) first.")

    if isinstance(context, list):
        messages = context
        model = None
        api_key = None
        api_base = None
    elif isinstance(context, dict):
        messages = context["messages"]
        model = context.get("model")
        api_key = context.get("api_key")
        api_base = context.get("api_base")
    else:
        raise TypeError(f"context must be list or dict, got {type(context)}")

    if not messages:
        return

    current_health = read_file(_health_path)

    updated_health, log_entries = consolidate(
        messages,
        current_health=current_health,
        model=model,
        api_key=api_key,
        api_base=api_base,
    )

    write_file(_health_path, updated_health)
    append_health_log(_health_log_path, log_entries)


def recall(context):
    """
    Retrieve health memory relevant to a query context.

    For the health memory system:
    - HEALTH.md is always returned in full (always-in-context).
    - health_log.jsonl is searched by keyword when a query is provided.

    Args:
        context: A query string or dict with optional "query" key.

    Returns:
        Dict with "health" (HEALTH.md contents), "health_log" (full log),
        and optionally "health_log_matches" (matching entries).
    """
    if _data_dir is None:
        raise RuntimeError("Memory not initialized. Call init(data_dir) first.")

    health_content = read_file(_health_path)
    log_content = read_file(_health_log_path)

    result = {
        "health": health_content,
        "health_log": log_content,
    }

    if isinstance(context, (str, dict)):
        query = context if isinstance(context, str) else context.get("query", "")
        if query and log_content:
            iso_date = _iso_from_natural_date(query)
            search_terms = [query.lower()]
            if iso_date:
                search_terms.append(iso_date.lower())
            # Morphological roots for word-level matching
            for root in _morph_terms(query):
                search_terms.append(root)

            matching = []
            for line in log_content.strip().split("\n"):
                if not line.strip():
                    continue
                line_lower = line.lower()
                if any(term in line_lower for term in search_terms):
                    try:
                        matching.append(json.loads(line))
                    except json.JSONDecodeError:
                        matching.append({"content": line})
            result["health_log_matches"] = matching

    return result

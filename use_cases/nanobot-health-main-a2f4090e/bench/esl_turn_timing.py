"""ESL-Bench ingest: map each user message to ``NANOBOT_CURRENT_TIME`` (ISO local).

Rules (aligned with ``mix_profile_timeline_user_turns.py`` output):

- **Merge day** (``mixing.profile_merge_date``, default ``2026-01-01``) when the turn has
  ``profile_message_count`` **or** ``turn_kind == "mixed"`` / ``"profile"`` with only
  profile lines: first ``N`` user messages use **11:00 + 30 minutes × index**; remaining
  messages (timeline tail) start **1 hour after** the last profile slot, then **+1 hour**
  per message.
- **Plain timeline days**: **11:00 + 1 hour × index** for each user message in that turn.
- **Profile-only turn** without ``profile_merge_date`` match (e.g. legacy
  ``user_turn_mixed.json``): if ``turn_kind == "profile"``, **30 minutes** from 11:00 for
  each message on ``effective_date`` (``date`` or carried ``last_date``).
- **Fallback**: if no calendar date applies, ``current_time`` is ``None``. If date exists but
  rules do not apply, use **noon** ``{date}T12:00:00``.

Used by ``run_agent.py`` and ``eval_agent.py`` (``--ingest-mixed``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

DAY_START_MIN = 11 * 60
PROFILE_STEP_MIN = 30
TIMELINE_STEP_MIN = 60


def profile_merge_date_from_doc(mixed: dict[str, Any]) -> str:
    m = mixed.get("mixing")
    if isinstance(m, dict):
        d = m.get("profile_merge_date")
        if isinstance(d, str) and d.strip():
            return d.strip()
    return "2026-01-01"


def _iso_from_date_and_minutes(date_str: str, minutes_from_midnight: int) -> str:
    base = datetime.strptime(date_str, "%Y-%m-%d")
    dt = base + timedelta(minutes=minutes_from_midnight)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _user_messages_in_turn(turn: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    for j, m in enumerate(turn.get("messages") or []):
        if not isinstance(m, dict):
            continue
        if m.get("role") != "user" or not isinstance(m.get("content"), str):
            continue
        if not m["content"].strip():
            continue
        out.append((j, m))
    return out


def _n_profile_for_turn(turn: dict[str, Any], num_user_msgs: int) -> int:
    raw = turn.get("profile_message_count")
    if raw is not None:
        return max(0, int(raw))
    kind = turn.get("turn_kind")
    if kind == "profile":
        return num_user_msgs
    return 0


def nanobot_current_time_for_user_message(
    *,
    effective_date: str | None,
    idx_in_user_msgs: int,
    turn: dict[str, Any],
    merge_date: str,
) -> str | None:
    if not effective_date:
        return None

    user_list = _user_messages_in_turn(turn)
    n_user = len(user_list)
    n_prof = _n_profile_for_turn(turn, n_user)
    kind = turn.get("turn_kind")

    if effective_date == merge_date and n_prof > 0 and kind in ("mixed", "profile"):
        if idx_in_user_msgs < n_prof:
            mins = DAY_START_MIN + PROFILE_STEP_MIN * idx_in_user_msgs
            return _iso_from_date_and_minutes(effective_date, mins)
        tl_i = idx_in_user_msgs - n_prof
        last_prof_start = DAY_START_MIN + PROFILE_STEP_MIN * (n_prof - 1)
        tl0 = last_prof_start + TIMELINE_STEP_MIN
        mins = tl0 + TIMELINE_STEP_MIN * tl_i
        return _iso_from_date_and_minutes(effective_date, mins)

    if effective_date == merge_date and kind == "timeline" and n_prof == 0:
        mins = DAY_START_MIN + TIMELINE_STEP_MIN * idx_in_user_msgs
        return _iso_from_date_and_minutes(effective_date, mins)

    if kind == "profile" and n_prof > 0 and effective_date != merge_date:
        mins = DAY_START_MIN + PROFILE_STEP_MIN * idx_in_user_msgs
        return _iso_from_date_and_minutes(effective_date, mins)

    if kind in (None, "timeline") or (kind == "mixed" and n_prof == 0):
        mins = DAY_START_MIN + TIMELINE_STEP_MIN * idx_in_user_msgs
        return _iso_from_date_and_minutes(effective_date, mins)

    return f"{effective_date}T12:00:00"


def iter_user_messages_with_current_time(
    mixed: dict[str, Any],
) -> list[tuple[str, str, str | None]]:
    """(turn_label, content, nanobot_current_time or None)."""
    merge_date = profile_merge_date_from_doc(mixed)
    out: list[tuple[str, str, str | None]] = []
    last_date: str | None = None

    for i, turn in enumerate(mixed.get("turns") or []):
        if not isinstance(turn, dict):
            continue
        d = turn.get("date")
        if isinstance(d, str) and d.strip():
            last_date = d.strip()
        effective = last_date

        tid = turn.get("turn_id") or turn.get("date") or f"turn_{i}"
        user_entries = _user_messages_in_turn(turn)

        for idx_in_turn, (j, msg) in enumerate(user_entries):
            c = msg["content"].strip()
            ct = nanobot_current_time_for_user_message(
                effective_date=effective,
                idx_in_user_msgs=idx_in_turn,
                turn=turn,
                merge_date=merge_date,
            )
            out.append((f"{tid}#m{j}", c, ct))

    return out


def last_ingest_nanobot_current_time(mixed: dict[str, Any]) -> str | None:
    items = iter_user_messages_with_current_time(mixed)
    if not items:
        return None
    return items[-1][2]


def ingest_time_rules_summary() -> str:
    return (
        "ingest NANOBOT_CURRENT_TIME: merge-day profile 11:00+30m per message, then timeline "
        "+1h steps; other days timeline 11:00+1h per message (see bench/esl_turn_timing.py)."
    )

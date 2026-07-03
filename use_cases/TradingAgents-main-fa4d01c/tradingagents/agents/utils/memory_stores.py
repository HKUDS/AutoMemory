"""Memory store primitives for TradingAgents.

Pure disk I/O — no LLM calls, no agent internals.
All paths are rooted at ~/.tradingagents/memory/.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_MEMORY_ROOT = Path(os.path.expanduser("~")) / ".tradingagents" / "memory"
_AGENT_DIR = _MEMORY_ROOT / "agent"
_USER_DIR = _MEMORY_ROOT / "user"

# Per-process flag: macro notes refreshed at most once per Python process.
_macro_refresh_done: bool = False


def _ensure(path: Path) -> Path:
    """Create directory (and parents) if missing, return the path."""
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Type C: Analyst Report Cache
# ---------------------------------------------------------------------------

class AnalystCache:
    """Exact-key cache for analyst reports keyed by (ticker, trade_date, analyst_type).

    Layout: ~/.tradingagents/memory/agent/analyst_cache/<TICKER>/<DATE>/<type>.json
    """

    _BASE = _AGENT_DIR / "analyst_cache"

    @classmethod
    def _path(cls, ticker: str, trade_date: str, analyst_type: str) -> Path:
        return cls._BASE / ticker / trade_date / f"{analyst_type}.json"

    @classmethod
    def check(cls, ticker: str, trade_date: str, analyst_type: str) -> Optional[str]:
        """Return cached report string, or None on miss."""
        p = cls._path(ticker, trade_date, analyst_type)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            report = data.get("report", "")
            return report if report else None
        except Exception:
            return None

    @classmethod
    def write(
        cls,
        ticker: str,
        trade_date: str,
        analyst_type: str,
        report: str,
        model: str = "",
    ) -> None:
        """Persist analyst report to cache (overwrites any prior entry)."""
        if not report or not report.strip():
            return
        p = cls._path(ticker, trade_date, analyst_type)
        _ensure(p.parent)
        record = {
            "report": report,
            "written_at": datetime.utcnow().isoformat(),
            "model": model,
        }
        p.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Type K: Ticker Knowledge Card
# ---------------------------------------------------------------------------

class KnowledgeCard:
    """Per-ticker markdown knowledge card.

    Layout: ~/.tradingagents/memory/agent/ticker_knowledge/<TICKER>.md
    """

    _BASE = _AGENT_DIR / "ticker_knowledge"

    @classmethod
    def _path(cls, ticker: str) -> Path:
        return cls._BASE / f"{ticker}.md"

    @classmethod
    def read(cls, ticker: str) -> str:
        """Return card body as string, or empty string if not found."""
        p = cls._path(ticker)
        if not p.exists():
            return ""
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return ""

    @classmethod
    def write(cls, ticker: str, content: str) -> None:
        """Overwrite card with new content."""
        if not content:
            return
        p = cls._path(ticker)
        _ensure(p.parent)
        p.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Type D: Decisions Log
# ---------------------------------------------------------------------------

_DECISIONS_CAP = 500  # max records per ticker (ring buffer)


class DecisionsLog:
    """Per-ticker append-only JSONL decisions log.

    Layout: ~/.tradingagents/memory/agent/recommendations/<TICKER>.jsonl
    Schema per record: {trade_date, ticker, rating, one_line_thesis, key_risks: [str]}
    """

    _BASE = _AGENT_DIR / "recommendations"

    @classmethod
    def _path(cls, ticker: str) -> Path:
        return cls._BASE / f"{ticker}.jsonl"

    @classmethod
    def read_last_n(cls, ticker: str, n: int = 3) -> List[Dict]:
        """Return the n most-recent decision records by trade_date (oldest first)."""
        if n <= 0:
            return []
        p = cls._path(ticker)
        if not p.exists():
            return []
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except Exception:
                pass
        records.sort(key=lambda r: r.get("trade_date", ""))
        return records[-n:]

    @classmethod
    def append(cls, ticker: str, record: Dict) -> None:
        """Append one decision record; deduplicate by trade_date; enforce ring-buffer cap."""
        _ensure(cls._BASE)
        p = cls._path(ticker)
        if p.exists():
            raw_lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        else:
            raw_lines = []

        # Deduplicate: remove any existing entry for the same trade_date
        trade_date = record.get("trade_date", "")
        kept = []
        for l in raw_lines:
            try:
                existing = json.loads(l)
                if existing.get("trade_date") == trade_date:
                    continue
            except Exception:
                pass
            kept.append(l)

        kept.append(json.dumps(record, ensure_ascii=False))

        # Enforce cap: sort by trade_date then keep the N most recent,
        # so eviction policy matches the date-order read_last_n guarantee.
        if len(kept) > _DECISIONS_CAP:
            parsed = []
            for l in kept:
                try:
                    parsed.append((json.loads(l).get("trade_date", ""), l))
                except Exception:
                    parsed.append(("", l))
            parsed.sort(key=lambda t: t[0])
            kept = [l for _, l in parsed[-_DECISIONS_CAP:]]

        p.write_text("\n".join(kept) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Type U: User Context (read-only by agent)
# ---------------------------------------------------------------------------

class UserContext:
    """Read-only user profile and portfolio context.

    Layout:
      ~/.tradingagents/memory/user/portfolio.json
      ~/.tradingagents/memory/user/profile.md
    """

    @classmethod
    def read(cls, ticker: str = "") -> str:
        """Return formatted portfolio + profile block, or empty string if unavailable."""
        portfolio_path = _USER_DIR / "portfolio.json"
        profile_path = _USER_DIR / "profile.md"

        portfolio_block = ""
        if portfolio_path.exists():
            try:
                data = json.loads(portfolio_path.read_text(encoding="utf-8"))
                cash = float(data.get("cash_balance", 0.0))
                positions = data.get("positions", [])
                positions_value = sum(
                    float(p.get("qty", 0)) * float(p.get("avg_cost", 0))
                    for p in positions
                )
                total_nav = cash + positions_value
                nav_pct = (cash / total_nav * 100) if total_nav > 0 else 100.0

                ticker_pos = None
                if ticker:
                    for pos in positions:
                        if pos.get("ticker", "").upper() == ticker.upper():
                            ticker_pos = pos
                            break

                lines = [f"Cash: ${cash:,.2f} ({nav_pct:.1f}% of NAV)"]
                if ticker_pos:
                    qty = float(ticker_pos.get("qty", 0))
                    avg_cost = float(ticker_pos.get("avg_cost", 0))
                    lines.append(
                        f"Current position in {ticker}: {qty:.0f} shares @ avg ${avg_cost:.2f}"
                    )
                elif ticker:
                    lines.append(f"No current position in {ticker}.")

                portfolio_block = "\n".join(lines)
            except Exception:
                portfolio_block = "(portfolio data unavailable)"

        profile_block = ""
        if profile_path.exists():
            try:
                profile_block = profile_path.read_text(encoding="utf-8").strip()
            except Exception:
                profile_block = "(profile unavailable)"

        if not portfolio_block and not profile_block:
            return ""

        parts = ["## Your portfolio context"]
        if portfolio_block:
            parts.append(portfolio_block)
        if profile_block:
            parts.append("\n## User profile & constraints")
            parts.append(profile_block)
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Type M: Macro Daily Notes (disk I/O only; LLM refresh lives in self_reflection.py)
# ---------------------------------------------------------------------------

_MACRO_WEEKS_CAP = 8  # keep at most this many weekly H2 sections


class MacroNotes:
    """Rolling macro notes markdown file; weekly H2 sections.

    Layout: ~/.tradingagents/memory/agent/macro_notes.md
    """

    _PATH = _AGENT_DIR / "macro_notes.md"

    @classmethod
    def read_recent(cls, n_weeks: int = 2) -> str:
        """Return latest n_weeks H2 sections as a markdown block, or empty string."""
        if n_weeks <= 0:
            return ""
        if not cls._PATH.exists():
            return ""
        try:
            content = cls._PATH.read_text(encoding="utf-8")
        except Exception:
            return ""
        sections = _split_h2_sections(content)
        if not sections:
            return ""
        sections.sort(key=lambda t: t[0])
        recent = sections[-n_weeks:]
        return "\n\n".join(f"## {header}\n{body}" for header, body in recent)

    @classmethod
    def get_last_update_date(cls) -> Optional[str]:
        """Return the ISO date string from 'Last updated: YYYY-MM-DD' in the most recent section."""
        if not cls._PATH.exists():
            return None
        try:
            content = cls._PATH.read_text(encoding="utf-8")
        except Exception:
            return None
        matches = list(re.finditer(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", content))
        return matches[-1].group(1) if matches else None

    @classmethod
    def write_section(
        cls, iso_week: str, bullets: List[str], trade_date: str
    ) -> None:
        """Write or replace the H2 section for iso_week, then cap at _MACRO_WEEKS_CAP."""
        _ensure(_AGENT_DIR)

        new_body = "\n".join(f"- {b}" for b in bullets) + f"\n\nLast updated: {trade_date}"

        content = ""
        if cls._PATH.exists():
            try:
                content = cls._PATH.read_text(encoding="utf-8")
            except Exception:
                pass

        sections = _split_h2_sections(content)

        # Replace the matching week section or append a new one
        replaced = False
        new_sections: List[Tuple[str, str]] = []
        for h, b in sections:
            if h == iso_week:
                new_sections.append((iso_week, new_body))
                replaced = True
            else:
                new_sections.append((h, b))
        if not replaced:
            new_sections.append((iso_week, new_body))

        # Sort by ISO week string (lexicographic order matches chronological for YYYY-Www)
        new_sections.sort(key=lambda t: t[0])

        # Enforce cap (keep the N most recent weeks after sorting)
        if len(new_sections) > _MACRO_WEEKS_CAP:
            new_sections = new_sections[-_MACRO_WEEKS_CAP:]

        assembled = "\n\n".join(f"## {h}\n{b}" for h, b in new_sections)
        cls._PATH.write_text(assembled + "\n", encoding="utf-8")


def _split_h2_sections(content: str) -> List[Tuple[str, str]]:
    """Split markdown into (header_text, body) pairs for H2 (##) sections."""
    parts = re.split(r"^## (.+)$", content, flags=re.MULTILINE)
    # parts layout: [preamble, header1, body1, header2, body2, ...]
    sections: List[Tuple[str, str]] = []
    i = 1
    while i + 1 < len(parts):
        header = parts[i].strip()
        body = parts[i + 1].strip()
        sections.append((header, body))
        i += 2
    return sections


def should_refresh_macro(trade_date: str) -> bool:
    """Return True if macro notes should be refreshed.

    Conditions: process has not refreshed yet AND the on-disk notes are stale
    (last-update date < trade_date).
    """
    global _macro_refresh_done
    if _macro_refresh_done:
        return False
    last_update = MacroNotes.get_last_update_date()
    if last_update is None:
        return True
    try:
        last_dt = datetime.strptime(last_update, "%Y-%m-%d").date()
        trade_dt = datetime.strptime(trade_date, "%Y-%m-%d").date()
        return trade_dt > last_dt
    except Exception:
        return True


def mark_macro_refresh_done() -> None:
    """Mark that macro notes were refreshed for this process (prevents repeat calls)."""
    global _macro_refresh_done
    _macro_refresh_done = True

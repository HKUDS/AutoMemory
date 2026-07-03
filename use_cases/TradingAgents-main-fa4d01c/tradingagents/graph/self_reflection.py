"""LLM-based post-propagate memory operations for TradingAgents.

Contains:
  - SelfReflector   : end-of-session self-reflection (no realized returns).
  - KnowledgeCardWriter : LLM merge of per-ticker knowledge card.
  - MacroRefresher  : fetch global news + rewrite macro notes section.
"""

import re
from datetime import datetime
from typing import Any, Callable, Dict, List

from tradingagents.agents.utils.memory_stores import (
    KnowledgeCard,
    MacroNotes,
    mark_macro_refresh_done,
    should_refresh_macro,
)


# ---------------------------------------------------------------------------
# Self Reflector (Type R write — no realized returns)
# ---------------------------------------------------------------------------

_SELF_REFLECT_SYSTEM = """You are an expert financial analyst conducting a consistency review of this trading session.
Do NOT fabricate outcome-based lessons — you have no realized returns yet.

Your task: identify at most 3 *specific, verifiable* issues from the debate text, such as:
- A risk explicitly raised by the Bear or Conservative analyst that the final decision ignored.
- A direct contradiction between two analyst reports that was left unresolved.
- A logical gap in the investment plan (e.g., PM recommends Buy but cites no catalyst).

If you find no clear, specific issue, output exactly: No issues found.

For each real issue found, output exactly:
SITUATION: <one sentence grounding the domain context first (include the company, sector, and specific metric/risk named, e.g. "Semiconductor company with ~60% data-center revenue facing supply chain risk") then state the debate-level issue>
LESSON: <one actionable sentence for future similar situations>

Critical: the SITUATION must include enough domain-specific vocabulary (company type, sector, key metric, risk type) so that it can be retrieved later by keyword search against similar market contexts.
Only emit a SITUATION/LESSON pair if it cites a verifiable detail from the provided text.
Limit output to 3 pairs maximum."""


class SelfReflector:
    """Post-propagate self-reflection: populates BM25 buckets without realized returns."""

    def __init__(self, quick_thinking_llm: Any) -> None:
        self.llm = quick_thinking_llm

    def run(self, state: Dict, memories: Dict) -> None:
        """Run self-reflection and add lessons to BM25 memory buckets.

        Args:
            state:    final_state dict from propagate().
            memories: {name: FinancialSituationMemory} — all five buckets.
        """
        market_report = state.get("market_report", "")
        sentiment_report = state.get("sentiment_report", "")
        news_report = state.get("news_report", "")
        fundamentals_report = state.get("fundamentals_report", "")
        invest_debate = state.get("investment_debate_state", {})
        risk_debate = state.get("risk_debate_state", {})
        final_decision = state.get("final_trade_decision", "")

        situation = (
            f"{market_report}\n\n{sentiment_report}\n\n"
            f"{news_report}\n\n{fundamentals_report}"
        )
        debate_context = (
            f"Bull history:\n{invest_debate.get('bull_history', '')}\n\n"
            f"Bear history:\n{invest_debate.get('bear_history', '')}\n\n"
            f"Risk debate:\n{risk_debate.get('history', '')}\n\n"
            f"Final PM decision:\n{final_decision}"
        )

        try:
            response = self.llm.invoke(
                [
                    ("system", _SELF_REFLECT_SYSTEM),
                    (
                        "human",
                        f"Analyst reports:\n{situation}\n\nDebate:\n{debate_context}",
                    ),
                ]
            ).content
        except Exception:
            return

        if "No issues found" in response:
            return

        pairs = _parse_situation_lesson_pairs(response)
        if not pairs:
            return

        for mem in memories.values():
            mem.add_situations(pairs)


_KC_SECTION_HEADERS = {
    "## Identity",
    "## Stable Fundamentals",
    "## Recent Technical Posture",
    "## Recent PM Ratings",
}
_KC_HEADER_RE = re.compile(r"^#{1,6}\s*(##\s+)?", re.MULTILINE)


_LAST_UPDATED_RE = re.compile(r"^Last updated:.*$", re.IGNORECASE | re.MULTILINE)


def _clean_knowledge_card(text: str) -> str:
    """Strip LLM preamble before the first ## section, normalise section headers,
    and remove any 'Last updated:' lines the LLM copied from the existing card
    (the caller appends its own authoritative timestamp afterward).

    Handles:
    1. Preamble title lines before "## Identity" (e.g. "### Updated Card for TICKER").
    2. Nested headers like "#### ## Identity" instead of "## Identity".
    3. Trailing or inline "Last updated: ..." lines carried over from the input card.
    """
    lines = text.splitlines()

    # Normalise "#### ## Identity" → "## Identity" style nesting
    cleaned: List[str] = []
    for line in lines:
        stripped = line.strip()
        # Match lines like "### ## Identity" or "#### ## Stable Fundamentals"
        m = re.match(r"^#{2,6}\s+(## \w.*)", stripped)
        if m:
            cleaned.append(m.group(1))
        else:
            cleaned.append(line)

    # Drop everything before the first recognised ## section header
    first_section = None
    for i, line in enumerate(cleaned):
        if line.strip() in _KC_SECTION_HEADERS:
            first_section = i
            break

    if first_section is not None:
        cleaned = cleaned[first_section:]

    # Remove any "Last updated: ..." lines the LLM copied verbatim from the card
    cleaned = [ln for ln in cleaned if not _LAST_UPDATED_RE.match(ln.strip())]

    return "\n".join(cleaned).rstrip()


def _parse_situation_lesson_pairs(text: str) -> List[tuple]:
    """Extract (situation, lesson) pairs from self-reflection LLM output."""
    situation_re = re.compile(r"SITUATION:\s*(.+?)(?=LESSON:|SITUATION:|$)", re.DOTALL)
    lesson_re = re.compile(r"LESSON:\s*(.+?)(?=SITUATION:|LESSON:|$)", re.DOTALL)
    situations = [m.group(1).strip() for m in situation_re.finditer(text)]
    lessons = [m.group(1).strip() for m in lesson_re.finditer(text)]
    return [(s, l) for s, l in zip(situations, lessons) if s and l]


# ---------------------------------------------------------------------------
# Knowledge Card Writer (Type K write)
# ---------------------------------------------------------------------------

_KC_MERGE_SYSTEM = """You are maintaining a compact per-ticker knowledge card (target ≤2 KB).
Given the existing card and this session's analyst reports plus the final PM rating, produce an updated card.

Rules:
- ## Identity: keep unchanged unless the session reveals a factual correction.
- ## Stable Fundamentals: replace with the session's most recent numbers.
- ## Recent Technical Posture: ≤8 dated bullet points. Drop bullets older than 30 days \
relative to the session date. Prepend any new bullets.
- ## Recent PM Ratings: prepend the new rating using format "[DATE] RATING — rationale (≤10 words)". Keep ≤5 entries. Do NOT use "- DATE:" dash-date bullet format (that format is reserved for the decisions log).

Format rules (STRICT):
- The very first line of your output MUST be "## Identity" — no title, no preamble, no "Updated card for ..." line before it.
- Every section header must be exactly "## Identity", "## Stable Fundamentals", "## Recent Technical Posture", "## Recent PM Ratings" — no extra #, no nesting.
- Do NOT wrap output in code fences (no ``` or ~~~).
- Do NOT add any explanatory text, comments, or lines that are not part of the card itself."""

_KC_SKELETON = """\
## Identity
{ticker} — (identity to be filled from session)

## Stable Fundamentals
(to be filled from session)

## Recent Technical Posture
(to be filled from session)

## Recent PM Ratings
(to be filled from session)
"""


class KnowledgeCardWriter:
    """Merge session outputs into the per-ticker knowledge card (one LLM call)."""

    def __init__(self, quick_thinking_llm: Any) -> None:
        self.llm = quick_thinking_llm

    def merge(self, state: Dict, ticker: str, trade_date: str) -> None:
        """Read existing card, merge with session reports, write back."""
        market_report = state.get("market_report", "")
        fundamentals_report = state.get("fundamentals_report", "")
        final_decision = state.get("final_trade_decision", "")

        existing_card = KnowledgeCard.read(ticker) or _KC_SKELETON.format(ticker=ticker)

        human_msg = (
            f"Session date: {trade_date}\nTicker: {ticker}\n\n"
            f"=== Existing card ===\n{existing_card}\n\n"
            f"=== Market report (latest technical) ===\n{market_report[:3000]}\n\n"
            f"=== Fundamentals report ===\n{fundamentals_report[:3000]}\n\n"
            f"=== Final PM rating ===\n{final_decision[:500]}"
        )

        try:
            new_card = self.llm.invoke(
                [("system", _KC_MERGE_SYSTEM), ("human", human_msg)]
            ).content.strip()
        except Exception:
            return

        if new_card:
            new_card = _clean_knowledge_card(new_card)
            KnowledgeCard.write(ticker, new_card + f"\n\nLast updated: {trade_date}")


# ---------------------------------------------------------------------------
# Macro Refresher (Type M write — at most once per process)
# ---------------------------------------------------------------------------

_MACRO_REFRESH_SYSTEM = """You are summarizing global macroeconomic news into a compact weekly briefing.
Given raw news articles, produce ≤10 bullet points covering:
global themes, macro risks, central bank signals, and sector tailwinds.
Each bullet: max 2 sentences. Focus on information relevant to equity trading.
Output ONLY the bullet points (one per line, starting with '- '). No headers, no intro text."""

_NUMBERED_BULLET_RE = re.compile(r"^\s*\d+[.)]\s+(.+)")
_DASH_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.+)")


def _extract_macro_bullets(text: str) -> List[str]:
    """Extract bullet text from LLM output, tolerating format variations.

    Handles:
    - '- bullet text' (canonical)
    - '* bullet text' or '• bullet text'
    - '1. bullet text' or '1) bullet text' (numbered lists)
    - Plain prose lines (fallback: treat non-empty lines as bullets)
    """
    bullets: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _DASH_BULLET_RE.match(line)
        if m:
            bullets.append(m.group(1).strip())
            continue
        m = _NUMBERED_BULLET_RE.match(line)
        if m:
            bullets.append(m.group(1).strip())
            continue

    # Fallback: if still nothing, treat every non-empty line as a bullet (truncate at 10)
    if not bullets:
        bullets = [l.strip() for l in text.splitlines() if l.strip()][:10]

    return bullets[:10]


class MacroRefresher:
    """Fetch global news and refresh the macro notes file (once per process)."""

    def __init__(self, quick_thinking_llm: Any, get_global_news_fn: Callable) -> None:
        """
        Args:
            quick_thinking_llm: LLM for summarization.
            get_global_news_fn: callable(curr_date, look_back_days, limit) -> str
        """
        self.llm = quick_thinking_llm
        self.get_global_news = get_global_news_fn

    def refresh_if_stale(self, trade_date: str) -> None:
        """Refresh macro notes if stale relative to trade_date; mark process flag."""
        if not should_refresh_macro(trade_date):
            return

        try:
            raw_news = self.get_global_news(
                curr_date=trade_date, look_back_days=7, limit=10
            )
        except Exception:
            return

        try:
            response = self.llm.invoke(
                [
                    ("system", _MACRO_REFRESH_SYSTEM),
                    ("human", f"News articles:\n{raw_news}"),
                ]
            ).content
        except Exception:
            return

        bullets = _extract_macro_bullets(response)
        if not bullets:
            import warnings
            warnings.warn(
                f"MacroRefresher: LLM produced no extractable bullet points for {trade_date}. "
                "Check the LLM response format. Skipping macro write.",
                RuntimeWarning,
                stacklevel=2,
            )
            return

        try:
            dt = datetime.strptime(trade_date, "%Y-%m-%d")
            cal = dt.isocalendar()
            iso_week = f"{cal[0]}-W{cal[1]:02d}"
        except Exception:
            iso_week = trade_date

        MacroNotes.write_section(iso_week, bullets, trade_date)
        mark_macro_refresh_done()

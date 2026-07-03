"""Functional tests for the TradingAgents memory system.

Run from the repo root:
    python -m pytest tradingagents-memory/tests/test_memory.py -v

Notes:
  - Each test class targets **one** memory component (Types C, D, K, M, U, R)
    with round-trip / dedup / lifecycle checks.
  - Memory modules are loaded via importlib to avoid pulling the full
    tradingagents import chain (yfinance / stockstats) during store tests.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Module-level direct loads (bypass package __init__.py import chains)
# ---------------------------------------------------------------------------

def _load_module(rel_path: str, name: str):
    """Load a Python file directly without triggering package __init__."""
    full = _REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, full)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load rank_bm25 so that memory.py can import it
try:
    import rank_bm25  # noqa: F401
except ImportError:
    pytest.skip("rank_bm25 not installed", allow_module_level=True)

ms_module = _load_module(
    "tradingagents/agents/utils/memory_stores.py",
    "ta_memory_stores",
)
mem_module = _load_module(
    "tradingagents/agents/utils/memory.py",
    "ta_memory",
)
# Make sure mem_module can find rank_bm25 (it imports as 'from rank_bm25 import …')
# which is already in sys.modules after the try/except above.

# Convenience aliases
AnalystCache = ms_module.AnalystCache
DecisionsLog = ms_module.DecisionsLog
KnowledgeCard = ms_module.KnowledgeCard
MacroNotes = ms_module.MacroNotes
UserContext = ms_module.UserContext
_split_h2_sections = ms_module._split_h2_sections
should_refresh_macro = ms_module.should_refresh_macro
FinancialSituationMemory = mem_module.FinancialSituationMemory


# ---------------------------------------------------------------------------
# Type C: Analyst Cache
# ---------------------------------------------------------------------------

class TestAnalystCache:

    @pytest.fixture(autouse=True)
    def isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(AnalystCache, "_BASE", tmp_path / "analyst_cache")

    def test_miss_returns_none(self):
        assert AnalystCache.check("MISS", "2026-01-01", "market") is None

    def test_write_then_read(self):
        report = "This is the market report for AAPL."
        AnalystCache.write("AAPL", "2026-01-01", "market", report, model="test")
        assert AnalystCache.check("AAPL", "2026-01-01", "market") == report

    def test_overwrite_same_key(self):
        AnalystCache.write("X", "2026-01-01", "social", "v1")
        AnalystCache.write("X", "2026-01-01", "social", "v2")
        assert AnalystCache.check("X", "2026-01-01", "social") == "v2"

    def test_different_analysts_isolated(self):
        AnalystCache.write("X", "2026-01-01", "market", "market report")
        AnalystCache.write("X", "2026-01-01", "fundamentals", "fund report")
        assert AnalystCache.check("X", "2026-01-01", "market") == "market report"
        assert AnalystCache.check("X", "2026-01-01", "fundamentals") == "fund report"
        assert AnalystCache.check("X", "2026-01-01", "news") is None


# ---------------------------------------------------------------------------
# Type D: Decisions Log
# ---------------------------------------------------------------------------

class TestDecisionsLog:

    @pytest.fixture(autouse=True)
    def isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(DecisionsLog, "_BASE", tmp_path / "recommendations")

    def test_read_empty_returns_empty(self):
        assert DecisionsLog.read_last_n("NONE", n=3) == []

    def test_append_and_read(self):
        record = {
            "trade_date": "2026-01-01",
            "ticker": "AAPL",
            "rating": "BUY",
            "one_line_thesis": "Strong momentum.",
            "key_risks": ["rate risk"],
        }
        DecisionsLog.append("AAPL", record)
        records = DecisionsLog.read_last_n("AAPL", n=3)
        assert len(records) == 1
        assert records[0]["rating"] == "BUY"

    def test_deduplication_same_date(self):
        DecisionsLog.append("X", {"trade_date": "2026-01-01", "rating": "HOLD"})
        DecisionsLog.append("X", {"trade_date": "2026-01-01", "rating": "BUY"})
        records = DecisionsLog.read_last_n("X", n=5)
        assert len(records) == 1
        assert records[0]["rating"] == "BUY"

    def test_read_last_n_ordering(self):
        for i in range(5):
            DecisionsLog.append("Y", {"trade_date": f"2026-01-0{i+1}", "rating": f"R{i}"})
        records = DecisionsLog.read_last_n("Y", n=3)
        assert len(records) == 3
        assert records[-1]["rating"] == "R4"

    def test_ring_buffer_cap(self, monkeypatch):
        monkeypatch.setattr(ms_module, "_DECISIONS_CAP", 3)
        for i in range(5):
            DecisionsLog.append("Z", {"trade_date": f"2026-01-{i+1:02d}", "rating": f"R{i}"})
        records = DecisionsLog.read_last_n("Z", n=10)
        assert len(records) == 3


# ---------------------------------------------------------------------------
# Type K: Knowledge Card
# ---------------------------------------------------------------------------

class TestKnowledgeCard:

    @pytest.fixture(autouse=True)
    def isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(KnowledgeCard, "_BASE", tmp_path / "ticker_knowledge")

    def test_read_missing_returns_empty(self):
        assert KnowledgeCard.read("MISSING") == ""

    def test_write_then_read(self):
        content = "## Identity\nNVDA — NVIDIA\n\n## Stable Fundamentals\nRevenue: $100B"
        KnowledgeCard.write("NVDA", content)
        assert KnowledgeCard.read("NVDA") == content

    def test_overwrite(self):
        KnowledgeCard.write("NVDA", "v1")
        KnowledgeCard.write("NVDA", "v2")
        assert KnowledgeCard.read("NVDA") == "v2"


# ---------------------------------------------------------------------------
# Type M: Macro Notes
# ---------------------------------------------------------------------------

class TestMacroNotes:

    @pytest.fixture(autouse=True)
    def isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(MacroNotes, "_PATH", tmp_path / "macro_notes.md")
        monkeypatch.setattr(ms_module, "_macro_refresh_done", False)

    def test_read_missing_returns_empty(self):
        assert MacroNotes.read_recent() == ""
        assert MacroNotes.get_last_update_date() is None

    def test_write_and_read_section(self):
        MacroNotes.write_section("2026-W01", ["Bullet 1", "Bullet 2"], "2026-01-05")
        recent = MacroNotes.read_recent(n_weeks=2)
        assert "2026-W01" in recent
        assert "Bullet 1" in recent
        assert MacroNotes.get_last_update_date() == "2026-01-05"

    def test_multiple_weeks_only_latest(self):
        MacroNotes.write_section("2026-W01", ["Old news"], "2026-01-05")
        MacroNotes.write_section("2026-W02", ["New news"], "2026-01-12")
        recent = MacroNotes.read_recent(n_weeks=1)
        assert "New news" in recent
        assert "Old news" not in recent

    def test_update_same_week_replaces(self):
        MacroNotes.write_section("2026-W01", ["v1"], "2026-01-05")
        MacroNotes.write_section("2026-W01", ["v2"], "2026-01-06")
        content = MacroNotes._PATH.read_text()
        assert "v2" in content
        assert content.count("2026-W01") == 1

    def test_should_refresh_macro_stale(self):
        MacroNotes.write_section("2026-W01", ["old"], "2026-01-05")
        assert should_refresh_macro("2026-01-06") is True

    def test_should_refresh_macro_same_date(self):
        MacroNotes.write_section("2026-W16", ["current"], "2026-04-20")
        assert should_refresh_macro("2026-04-20") is False

    def test_process_flag_prevents_refresh(self, monkeypatch):
        monkeypatch.setattr(ms_module, "_macro_refresh_done", True)
        assert should_refresh_macro("2999-01-01") is False


# ---------------------------------------------------------------------------
# Internal helper: _split_h2_sections
# ---------------------------------------------------------------------------

class TestSplitH2Sections:
    def test_basic_split(self):
        content = "## Section 1\nbody1\n\n## Section 2\nbody2"
        sections = _split_h2_sections(content)
        assert len(sections) == 2
        assert sections[0][0] == "Section 1"
        assert "body1" in sections[0][1]

    def test_empty_content(self):
        assert _split_h2_sections("") == []


# ---------------------------------------------------------------------------
# Type U: User Context
# ---------------------------------------------------------------------------

class TestUserContext:

    @pytest.fixture(autouse=True)
    def isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ms_module, "_USER_DIR", tmp_path / "user")

    def test_empty_returns_empty(self):
        assert UserContext.read() == ""

    def test_portfolio_and_profile(self, tmp_path):
        user_dir = tmp_path / "user"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "portfolio.json").write_text(
            json.dumps({
                "cash_balance": 10000.0,
                "positions": [{"ticker": "AAPL", "qty": 50, "avg_cost": 200.0}],
            }),
            encoding="utf-8",
        )
        (user_dir / "profile.md").write_text(
            "Risk tolerance: moderate. No leverage.",
            encoding="utf-8",
        )
        ctx = UserContext.read(ticker="AAPL")
        assert "10,000" in ctx
        assert "50 shares" in ctx
        assert "moderate" in ctx

    def test_no_position_ticker(self, tmp_path):
        user_dir = tmp_path / "user"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "portfolio.json").write_text(
            json.dumps({"cash_balance": 5000.0, "positions": []}),
            encoding="utf-8",
        )
        ctx = UserContext.read(ticker="NVDA")
        assert "No current position in NVDA" in ctx


# ---------------------------------------------------------------------------
# Type R: FinancialSituationMemory (BM25 + JSONL persistence)
# ---------------------------------------------------------------------------

class TestFinancialSituationMemory:

    @pytest.fixture(autouse=True)
    def isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mem_module, "_REFLECTIONS_DIR", tmp_path / "reflections")

    def test_empty_returns_no_memories(self):
        m = FinancialSituationMemory("test_bucket")
        assert m.get_memories("any query") == []

    def test_add_and_retrieve(self):
        m = FinancialSituationMemory("test_bucket")
        m.add_situations([("tech stock momentum rising", "Buy on breakout.")])
        results = m.get_memories("tech stocks moving higher", n_matches=1)
        assert len(results) == 1
        assert "Buy" in results[0]["recommendation"]

    def test_persistence_across_instances(self, tmp_path, monkeypatch):
        """Second instance with same name should reload from JSONL."""
        refl_dir = tmp_path / "persist_reflections"
        monkeypatch.setattr(mem_module, "_REFLECTIONS_DIR", refl_dir)
        m1 = FinancialSituationMemory("persist_test")
        m1.add_situations([("oil prices falling energy sector down", "Avoid energy stocks.")])

        m2 = FinancialSituationMemory("persist_test")
        results = m2.get_memories("oil price decline energy", n_matches=1)
        assert len(results) == 1
        assert "energy" in results[0]["recommendation"].lower()

    def test_multiple_situations_best_match(self):
        m = FinancialSituationMemory("multi_test")
        m.add_situations([
            ("inflation rising bonds falling", "Reduce bond exposure."),
            ("tech earnings beat growth accelerating", "Increase tech allocation."),
            ("gold rising dollar falling risk-off", "Add defensive positions."),
        ])
        results = m.get_memories("technology earnings growing fast", n_matches=1)
        assert len(results) == 1
        assert "tech" in results[0]["recommendation"].lower()


def _openrouter_configured() -> bool:
    try:
        from dotenv import load_dotenv

        load_dotenv(_REPO_ROOT / ".env", override=False)
    except Exception:
        pass
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def _patch_production_memory_paths(monkeypatch, base):
    """Point production memory stores at ``base`` (must run before ``TradingAgentsGraph()``)."""
    import tradingagents.agents.utils.memory as pmem
    import tradingagents.agents.utils.memory_stores as pms

    agent = base / "agent"
    user = base / "user"
    refl = base / "reflections"
    agent.mkdir(parents=True, exist_ok=True)
    user.mkdir(parents=True, exist_ok=True)
    refl.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pms, "_AGENT_DIR", agent)
    monkeypatch.setattr(pms, "_USER_DIR", user)
    monkeypatch.setattr(pms.AnalystCache, "_BASE", agent / "analyst_cache")
    monkeypatch.setattr(pms.DecisionsLog, "_BASE", agent / "recommendations")
    monkeypatch.setattr(pms.KnowledgeCard, "_BASE", agent / "ticker_knowledge")
    monkeypatch.setattr(pms.MacroNotes, "_PATH", agent / "macro_notes.md")
    monkeypatch.setattr(pms, "_macro_refresh_done", False)
    monkeypatch.setattr(pmem, "_REFLECTIONS_DIR", refl)



if __name__ == "__main__":
    pytest.main([__file__, "-v"])

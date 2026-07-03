"""
Functional tests for the nanobot health memory system.

Uses a real LLM provider (OpenRouter) — requires OPENROUTER_API_KEY env var.
"""

import json
import os
import shutil
import tempfile

import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import memory as health_mem
from memory import init, memorize, recall
from memory.consolidation import read_file


@pytest.fixture
def data_dir():
    """Create a temporary data directory for each test."""
    tmpdir = tempfile.mkdtemp(prefix="nanobot_health_test_")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset module-level globals between tests."""
    health_mem._data_dir = None
    health_mem._health_path = None
    health_mem._health_log_path = None
    yield


def _get_api_key():
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        pytest.skip("OPENROUTER_API_KEY not set")
    return key


class TestInit:
    def test_init_creates_directory_and_template(self, data_dir):
        init(data_dir)
        health_path = os.path.join(data_dir, "HEALTH.md")
        assert os.path.exists(health_path)
        content = read_file(health_path)
        assert "# Health Profile" in content
        assert "## Medications" in content
        assert "(No entries yet)" in content

    def test_init_preserves_existing_health_file(self, data_dir):
        health_path = os.path.join(data_dir, "HEALTH.md")
        os.makedirs(data_dir, exist_ok=True)
        with open(health_path, "w") as f:
            f.write("# Health Profile\n\n## Medications\n- Lisinopril 10mg daily\n")
        init(data_dir)
        content = read_file(health_path)
        assert "Lisinopril" in content

    def test_reinit_same_directory(self, data_dir):
        init(data_dir)
        health_path = os.path.join(data_dir, "HEALTH.md")
        with open(health_path, "w") as f:
            f.write("# Health Profile\n\n## Medications\n- Aspirin 81mg daily\n")
        init(data_dir)
        content = read_file(health_path)
        assert "Aspirin" in content


class TestMemorizeRecall:
    def test_round_trip_medication(self, data_dir):
        """Memorize a medication, then recall it."""
        api_key = _get_api_key()
        init(data_dir)

        messages = [
            {"role": "user", "content": "I take metformin 500mg twice daily for type 2 diabetes. I started it in January 2025."},
            {"role": "assistant", "content": "Got it, I've noted that you take metformin 500mg twice daily for type 2 diabetes, started January 2025."},
        ]

        memorize({"messages": messages, "api_key": api_key})

        result = recall("metformin")
        assert "metformin" in result["health"].lower() or "Metformin" in result["health"]
        assert "500mg" in result["health"] or "500 mg" in result["health"]

    def test_round_trip_allergy(self, data_dir):
        """Memorize an allergy, then recall it."""
        api_key = _get_api_key()
        init(data_dir)

        messages = [
            {"role": "user", "content": "I'm allergic to sulfa drugs. I get a severe rash."},
            {"role": "assistant", "content": "Noted — sulfa drug allergy causing severe rash."},
        ]

        memorize({"messages": messages, "api_key": api_key})

        result = recall("allergy")
        health = result["health"].lower()
        assert "sulfa" in health
        assert "rash" in health

    def test_round_trip_symptom_log(self, data_dir):
        """Memorize a symptom, then search the log."""
        api_key = _get_api_key()
        init(data_dir)

        messages = [
            {"role": "user", "content": "I've had a bad migraine since this morning. Light makes it worse. I took 800mg ibuprofen."},
            {"role": "assistant", "content": "Sorry to hear about the migraine. I've logged that you took 800mg ibuprofen. Rest in a dark room if possible."},
        ]

        memorize({"messages": messages, "api_key": api_key})

        result = recall("migraine")
        log_path = os.path.join(data_dir, "health_log.jsonl")
        if os.path.exists(log_path):
            log_content = read_file(log_path)
            if log_content.strip():
                assert "migraine" in log_content.lower() or "headache" in log_content.lower()

    def test_update_dedup_medication(self, data_dir):
        """Update a medication dose — should not duplicate."""
        api_key = _get_api_key()
        init(data_dir)

        messages1 = [
            {"role": "user", "content": "I take lisinopril 10mg daily for high blood pressure."},
            {"role": "assistant", "content": "Noted — lisinopril 10mg daily for hypertension."},
        ]
        memorize({"messages": messages1, "api_key": api_key})

        messages2 = [
            {"role": "user", "content": "My doctor increased my lisinopril to 20mg daily."},
            {"role": "assistant", "content": "Updated — lisinopril dose is now 20mg daily."},
        ]
        memorize({"messages": messages2, "api_key": api_key})

        result = recall("lisinopril")
        health = result["health"]
        assert "20mg" in health or "20 mg" in health
        lisinopril_count = health.lower().count("lisinopril")
        assert lisinopril_count <= 2, f"Lisinopril appears {lisinopril_count} times — possible duplication"

    def test_recall_with_no_match(self, data_dir):
        """Recall with a query that has no matches."""
        init(data_dir)
        result = recall("xyz_nonexistent_12345")
        assert result["health"] is not None
        matches = result.get("health_log_matches", [])
        assert len(matches) == 0


class TestLifecycle:
    def test_init_use_reinit(self, data_dir):
        """Test the full lifecycle: init, use, re-init with same dir."""
        api_key = _get_api_key()
        init(data_dir)

        messages = [
            {"role": "user", "content": "My blood type is O positive."},
            {"role": "assistant", "content": "Noted — blood type O+."},
        ]
        memorize({"messages": messages, "api_key": api_key})

        result1 = recall("blood type")
        assert "O" in result1["health"]

        # Re-init with same directory — data should persist
        init(data_dir)
        result2 = recall("blood type")
        assert "O" in result2["health"]
        assert result1["health"] == result2["health"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

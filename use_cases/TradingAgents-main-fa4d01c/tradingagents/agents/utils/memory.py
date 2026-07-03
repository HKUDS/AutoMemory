"""Financial situation memory using BM25 for lexical similarity matching.

Uses BM25 (Best Matching 25) algorithm for retrieval — no API calls,
no token limits, works offline with any LLM provider.

Persistence: each named bucket is backed by a JSONL file at
~/.tradingagents/memory/agent/reflections/<name>.jsonl.
The BM25 index is rebuilt from the file on every __init__ call.
"""

import json
import os
import re
from pathlib import Path
from typing import List, Tuple

from rank_bm25 import BM25Okapi

_REFLECTIONS_DIR = (
    Path(os.path.expanduser("~")) / ".tradingagents" / "memory" / "agent" / "reflections"
)
_BUCKET_CAP = 500  # maximum records per JSONL file (ring buffer)


class FinancialSituationMemory:
    """Memory system for storing and retrieving financial situations using BM25.

    Every mutation (add_situations) is immediately flushed to the JSONL sidecar
    so memories survive process restarts.
    """

    def __init__(self, name: str, config: dict = None):
        """Initialize the memory system and load existing records from disk.

        Args:
            name:   Bucket identifier (e.g. 'bull_memory', 'bear_memory', …).
            config: Configuration dict (kept for API compatibility, not used here).
        """
        self.name = name
        self.documents: List[str] = []
        self.recommendations: List[str] = []
        self.bm25 = None

        # Attempt to load persisted records
        self._jsonl_path = _REFLECTIONS_DIR / f"{name}.jsonl"
        self._load_from_disk()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_from_disk(self) -> None:
        """Load situation/recommendation pairs from the JSONL sidecar."""
        if not self._jsonl_path.exists():
            return
        try:
            for line in self._jsonl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                situation = record.get("situation", "")
                recommendation = record.get("recommendation", "")
                if situation and recommendation:
                    self.documents.append(situation)
                    self.recommendations.append(recommendation)
        except Exception:
            pass
        self._rebuild_index()

    def _flush_to_disk(self) -> None:
        """Rewrite the full JSONL file from in-memory lists (enforces ring-buffer cap)."""
        try:
            _REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
            docs = self.documents[-_BUCKET_CAP:]
            recs = self.recommendations[-_BUCKET_CAP:]
            lines = [
                json.dumps({"situation": s, "recommendation": r}, ensure_ascii=False)
                for s, r in zip(docs, recs)
            ]
            self._jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # BM25 helpers
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text for BM25 indexing."""
        return re.findall(r"\b\w+\b", text.lower())

    def _rebuild_index(self) -> None:
        """Rebuild the BM25 index from the current document list."""
        if self.documents:
            tokenized = [self._tokenize(doc) for doc in self.documents]
            self.bm25 = BM25Okapi(tokenized)
        else:
            self.bm25 = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_situations(self, situations_and_advice: List[Tuple[str, str]]) -> None:
        """Add financial situations and their corresponding advice.

        Appends to in-memory lists, rebuilds BM25, then flushes to disk.

        Args:
            situations_and_advice: List of (situation, recommendation) tuples.
        """
        for situation, recommendation in situations_and_advice:
            self.documents.append(situation)
            self.recommendations.append(recommendation)

        # Enforce cap in-memory as well
        if len(self.documents) > _BUCKET_CAP:
            self.documents = self.documents[-_BUCKET_CAP:]
            self.recommendations = self.recommendations[-_BUCKET_CAP:]

        self._rebuild_index()
        self._flush_to_disk()

    def get_memories(self, current_situation: str, n_matches: int = 1) -> List[dict]:
        """Find matching recommendations using BM25 similarity.

        Args:
            current_situation: The current financial situation to match against.
            n_matches:         Number of top matches to return.

        Returns:
            List of dicts with keys: matched_situation, recommendation, similarity_score.
        """
        if not self.documents or self.bm25 is None:
            return []

        query_tokens = self._tokenize(current_situation)
        scores = self.bm25.get_scores(query_tokens)

        top_indices = sorted(
            range(len(scores)), key=lambda i: scores[i], reverse=True
        )[:n_matches]

        max_score = float(scores.max()) if len(scores) > 0 and scores.max() > 0 else 1.0
        results = []
        for idx in top_indices:
            normalized = scores[idx] / max_score if max_score > 0 else 0
            results.append(
                {
                    "matched_situation": self.documents[idx],
                    "recommendation": self.recommendations[idx],
                    "similarity_score": normalized,
                }
            )
        return results

    def clear(self) -> None:
        """Clear all stored memories (in-memory only; does not erase disk)."""
        self.documents = []
        self.recommendations = []
        self.bm25 = None


if __name__ == "__main__":
    matcher = FinancialSituationMemory("test_memory")

    example_data = [
        (
            "High inflation rate with rising interest rates and declining consumer spending",
            "Consider defensive sectors like consumer staples and utilities.",
        ),
        (
            "Tech sector showing high volatility with increasing institutional selling pressure",
            "Reduce exposure to high-growth tech stocks.",
        ),
    ]
    matcher.add_situations(example_data)

    recommendations = matcher.get_memories(
        "Market volatility in tech with rising rates", n_matches=2
    )
    for i, rec in enumerate(recommendations, 1):
        print(f"\nMatch {i}: score={rec['similarity_score']:.2f}")
        print(f"  Situation: {rec['matched_situation'][:60]}…")
        print(f"  Recommendation: {rec['recommendation'][:60]}…")

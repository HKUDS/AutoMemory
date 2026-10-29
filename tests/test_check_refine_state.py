"""Regression tests for score bounds; run with unittest discovery."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_refine_state", ROOT / "scaffolds" / "check_refine_state.py"
)
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class ScoreRangeTests(unittest.TestCase):
    def validate_score(self, key, value, skip_files=False):
        row = {"trace_id": "trace_01"}
        if key != "hit_rate":
            row.update(support_coverage=1, query_success=1)
        row[key] = value
        state = {
            "current_phase": 0,
            "max_phases": 1,
            "step_status": "ready",
            "runner_path": "",
            "memory_paths": {},
            "graduated_paths": [],
            "stop_decision": {"by": "critic", "decision": "continue"},
            "trace_registry": [],
            "phase_history": [{
                "phase": 0, "scores": [row], "new_traces": [],
                "weakness": [], "decision": "continue",
            }],
        }
        # Isolate score validation while exercising the full state validator.
        # File/registry checks are tested separately from score domains.
        errors = CHECKER.validate(state, ROOT / "state.json", skip_files=skip_files)
        return [error for error in errors if ".scores[0]." + key in error]

    def test_out_of_range_and_nonfinite_scores_are_rejected(self):
        for key in ("support_coverage", "query_success", "hit_rate"):
            for value in (-0.1, 1.25, float("nan"), float("inf"), -float("inf")):
                for skip_files in (False, True):
                    with self.subTest(key=key, value=value, skip_files=skip_files):
                        self.assertEqual(self.validate_score(key, value, skip_files), [
                            f"phase_history[0].scores[0].{key} must be between 0 and 1"
                        ])

    def test_boundaries_and_fractional_scores_remain_valid(self):
        for key in ("support_coverage", "query_success", "hit_rate"):
            for value in (0, 1, 0.0, 1.0, 0.5, 0.8, 0.9):
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.validate_score(key, value, True), [])

    def test_nonnumeric_values_remain_rejected(self):
        for key in ("support_coverage", "query_success", "hit_rate"):
            for value in (True, False, None, "0.8", [], {}):
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.validate_score(key, value, True), [
                        f"phase_history[0].scores[0].{key} must be number"
                    ])


if __name__ == "__main__":
    unittest.main()

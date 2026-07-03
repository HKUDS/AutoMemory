#!/usr/bin/env python3
"""Pre-trace gate: validate worktraces/refine_state.json against the template.

This checker intentionally follows `refine_state_template.json` strictly:
- wrapper shape may be either flat state or `_meta` + `refine_state`
- `refine_state` must use only template fields
- `trace_registry`, `scores`, `weakness`, and `improvement` rows must use only
  template fields

Use `--skip-files` for scaffold-only checks when placeholder trace ids or files
do not exist yet.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ALLOWED_STEP_STATUSES = {
    "ready",
    "critic_finished",
    "improver_finished",
    "completed",
}
ALLOWED_DECISIONS = {"continue", "stop"}
HARNESS_ROOT = Path(__file__).resolve().parents[1]
ROOT_KEYS = {
    "current_phase",
    "max_phases",
    "step_status",
    "runner_path",
    "memory_paths",
    "graduated_paths",
    "stop_decision",
    "trace_registry",
    "phase_history",
}
STOP_DECISION_KEYS = {"by", "decision"}
TRACE_KEYS = {"trace_id", "memory_path", "focus", "created_in_phase"}
PHASE_KEYS = {"phase", "improvement", "scores", "new_traces", "weakness", "decision"}
SCORE_KEYS = {"trace_id", "support_coverage", "query_success", "hit_rate"}
WEAKNESS_KEYS = {"severity", "type", "description"}
MEMORY_PATH_KEYS = {"components", "eval_mode", "policy"}
ALLOWED_EVAL_MODES = {"qa", "hit_rate"}
ALLOWED_MEMORY_PATH_POLICIES = {"on-policy", "off-policy"}


def _is_number(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def reject_extra_keys(
    errors: list[str], obj: object, allowed: set[str], label: str
) -> None:
    if not isinstance(obj, dict):
        return
    extras = sorted(set(obj) - allowed)
    if extras:
        errors.append(f"{label} has unexpected keys: {extras}")


def resolve_path(raw: str, state_path: Path) -> Path:
    if raw.startswith("@Auto_Memory/"):
        # Resolve symbolic paths relative to the checked-out harness repo.
        return (HARNESS_ROOT / raw.removeprefix("@Auto_Memory/")).resolve()
    p = Path(raw)
    if p.is_absolute():
        return p
    base = state_path.parent.parent if state_path.parent.name == "worktraces" else state_path.parent
    return (base / p).resolve()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def unwrap_refine_state(data: dict) -> dict:
    """Accept flat refine_state.json or the documented template wrapper."""
    inner = data.get("refine_state")
    if isinstance(inner, dict) and (
        "current_phase" in inner or "step_status" in inner
    ):
        return inner
    return data


def validate_phase_scores(
    errors: list[str],
    scores: object,
    label: str,
    trace_ids: set[str],
    *,
    skip_files: bool,
) -> None:
    if not isinstance(scores, list):
        errors.append(f"{label}.scores must be a list")
        return

    for j, row in enumerate(scores):
        row_label = f"{label}.scores[{j}]"
        if not isinstance(row, dict):
            errors.append(f"{row_label} must be an object")
            continue
        reject_extra_keys(errors, row, SCORE_KEYS, row_label)
        trace_id = row.get("trace_id")
        if not isinstance(trace_id, str):
            errors.append(f"{row_label}.trace_id must be string")
        elif not skip_files and trace_id not in trace_ids:
            errors.append(f"{row_label}.trace_id not found in trace_registry: {trace_id}")
        has_qa = "support_coverage" in row or "query_success" in row
        has_hit_rate = "hit_rate" in row
        if not has_qa and not has_hit_rate:
            errors.append(
                f"{row_label} must have either (support_coverage + query_success) or hit_rate"
            )
        if has_qa:
            for key in ("support_coverage", "query_success"):
                if key not in row:
                    errors.append(f"{row_label} missing '{key}' (required when using qa metrics)")
                elif not _is_number(row[key]):
                    errors.append(f"{row_label}.{key} must be number")
        if has_hit_rate:
            if not _is_number(row["hit_rate"]):
                errors.append(f"{row_label}.hit_rate must be number")


def infer_trace_path(state_path: Path, trace_id: str) -> Path:
    return resolve_path(f"worktraces/traces/{trace_id}.json", state_path)


def validate_improvement_list(errors: list[str], improvement: object, label: str) -> None:
    if not isinstance(improvement, list):
        errors.append(f"{label}.improvement must be list")
        return
    for i, item in enumerate(improvement):
        if not isinstance(item, str):
            errors.append(f"{label}.improvement[{i}] must be string")


def validate(state: dict, state_path: Path, *, skip_files: bool = False) -> list[str]:
    errors: list[str] = []
    reject_extra_keys(errors, state, ROOT_KEYS, "refine_state")

    current_phase = state.get("current_phase")
    if not isinstance(current_phase, int) or current_phase < 0:
        errors.append("current_phase must be a non-negative integer")

    max_phases = state.get("max_phases")
    if not isinstance(max_phases, int) or max_phases <= 0:
        errors.append("max_phases must be a positive integer")
    elif isinstance(current_phase, int) and current_phase > max_phases:
        errors.append(
            f"current_phase={current_phase} must be at most max_phases={max_phases}"
        )

    step_status = state.get("step_status")
    if step_status not in ALLOWED_STEP_STATUSES:
        errors.append(f"step_status must be one of {sorted(ALLOWED_STEP_STATUSES)}")

    stop = state.get("stop_decision")
    if not isinstance(stop, dict):
        errors.append("stop_decision must be an object")
    else:
        reject_extra_keys(errors, stop, STOP_DECISION_KEYS, "stop_decision")
        for key in ("by", "decision"):
            if key not in stop:
                errors.append(f"stop_decision missing '{key}'")
        if stop.get("decision") not in ALLOWED_DECISIONS:
            errors.append("stop_decision.decision must be continue|stop")
        if stop.get("decision") == "stop" and stop.get("by") != "critic":
            errors.append("illegal stop_decision: only critic can stop")

    runner = state.get("runner_path")
    if not isinstance(runner, str):
        errors.append("runner_path must be a string")
    elif not runner.strip():
        if step_status != "ready":
            errors.append(
                "runner_path must be non-empty unless step_status is 'ready'"
            )
    elif not skip_files:
        stripped = runner.strip()
        if " " in stripped or "\t" in stripped:
            # Shell command (e.g. conda run … python …/run_traces.py), not a single fs path.
            pass
        else:
            runner_fs_path = resolve_path(runner, state_path)
            if not runner_fs_path.exists():
                errors.append(
                    f"runner_path does not exist: {runner} -> {runner_fs_path}"
                )

    memory_paths = state.get("memory_paths")
    valid_path_names: set[str] = set()
    if not isinstance(memory_paths, dict):
        errors.append("memory_paths must be a dict")
    else:
        for path_name, path_def in memory_paths.items():
            mp_label = f"memory_paths[{path_name!r}]"
            if not isinstance(path_def, dict):
                errors.append(f"{mp_label} must be an object")
                continue
            reject_extra_keys(errors, path_def, MEMORY_PATH_KEYS, mp_label)
            components = path_def.get("components")
            if not isinstance(components, list) or len(components) == 0:
                errors.append(f"{mp_label}.components must be a non-empty list")
            else:
                for ci, comp in enumerate(components):
                    if not isinstance(comp, str):
                        errors.append(f"{mp_label}.components[{ci}] must be string")
            eval_mode = path_def.get("eval_mode")
            if eval_mode not in ALLOWED_EVAL_MODES:
                errors.append(
                    f"{mp_label}.eval_mode must be one of {sorted(ALLOWED_EVAL_MODES)}"
                )
            policy_val = path_def.get("policy")
            if not isinstance(policy_val, str) or policy_val not in ALLOWED_MEMORY_PATH_POLICIES:
                errors.append(
                    f"{mp_label}.policy must be one of "
                    f"{sorted(ALLOWED_MEMORY_PATH_POLICIES)}"
                )
            valid_path_names.add(path_name)
        if step_status != "ready" and isinstance(current_phase, int) and current_phase > 0:
            if len(valid_path_names) == 0:
                errors.append(
                    "memory_paths must be non-empty after Phase 0 "
                    "(Phase 0 Critic sets path composition)"
                )

    graduated = state.get("graduated_paths")
    if not isinstance(graduated, list):
        errors.append("graduated_paths must be a list")
    else:
        for gi, gp in enumerate(graduated):
            if not isinstance(gp, str):
                errors.append(f"graduated_paths[{gi}] must be string")
            elif (
                valid_path_names
                and gp not in valid_path_names
            ):
                errors.append(
                    f"graduated_paths[{gi}] '{gp}' not found in memory_paths"
                )
        if len(graduated) != len(set(graduated)):
            errors.append("graduated_paths contains duplicates")

    registry = state.get("trace_registry")
    if not isinstance(registry, list):
        errors.append("trace_registry must be a list")
        registry = []

    if step_status != "ready" and len(registry) == 0:
        errors.append("trace_registry must be non-empty when step_status is not 'ready'")

    trace_ids: set[str] = set()
    for i, item in enumerate(registry):
        label = f"trace_registry[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        reject_extra_keys(errors, item, TRACE_KEYS, label)

        tid = item.get("trace_id")
        if not isinstance(tid, str):
            errors.append(f"{label}.trace_id must be string")
            tid = ""
        elif not skip_files and not tid.strip():
            errors.append(f"{label}.trace_id must be non-empty")

        memory_path = item.get("memory_path")
        if not isinstance(memory_path, str):
            errors.append(f"{label}.memory_path must be string")
        elif not skip_files and not memory_path.strip():
            errors.append(f"{label}.memory_path must be non-empty")
        elif (
            not skip_files
            and memory_path.strip()
            and valid_path_names
            and memory_path not in valid_path_names
            and memory_path != "e2e"
        ):
            errors.append(
                f"{label}.memory_path '{memory_path}' not found in "
                f"memory_paths or 'e2e'"
            )

        focus = item.get("focus")
        if not isinstance(focus, str):
            errors.append(f"{label}.focus must be string")

        created = item.get("created_in_phase")
        if not isinstance(created, int) or created < 0:
            errors.append(f"{label}.created_in_phase must be int >= 0")
        if (
            isinstance(created, int)
            and isinstance(current_phase, int)
            and created > current_phase
        ):
            errors.append(
                f"trace '{tid}' created_in_phase={created} > "
                f"state.current_phase={current_phase}"
            )

        if not tid:
            continue

        if tid in trace_ids:
            errors.append(f"duplicate trace_id in registry: {tid}")
            continue
        trace_ids.add(tid)

        if not skip_files:
            trace_path = infer_trace_path(state_path, tid)
            if not trace_path.exists():
                errors.append(f"trace file missing for '{tid}': {trace_path}")

    history = state.get("phase_history")
    if not isinstance(history, list):
        errors.append("phase_history must be a list")
        history = []
    expected_phase = 0
    seen_phases: set[int] = set()
    entries_by_phase: dict[int, dict] = {}
    for i, entry in enumerate(history):
        label = f"phase_history[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        reject_extra_keys(errors, entry, PHASE_KEYS, label)
        phase_num = entry.get("phase")
        if not isinstance(phase_num, int):
            errors.append(f"{label}.phase must be int")
            continue
        if phase_num in seen_phases:
            errors.append(f"duplicate phase_history phase={phase_num}")
        seen_phases.add(phase_num)
        if phase_num != expected_phase:
            errors.append(f"phase_history not continuous: expected {expected_phase}, got {phase_num}")
            expected_phase = phase_num
        expected_phase += 1
        entries_by_phase[phase_num] = entry

        validate_phase_scores(errors, entry.get("scores"), label, trace_ids, skip_files=skip_files)

        if "improvement" in entry:
            validate_improvement_list(errors, entry.get("improvement"), label)

        new_traces = entry.get("new_traces")
        if not isinstance(new_traces, list):
            errors.append(f"{label}.new_traces must be list")
        else:
            for j, trace_id in enumerate(new_traces):
                if not isinstance(trace_id, str):
                    errors.append(f"{label}.new_traces[{j}] must be string")
                elif not skip_files and trace_id not in trace_ids:
                    errors.append(f"{label}.new_traces[{j}] not found in trace_registry: {trace_id}")

        weakness = entry.get("weakness")
        if not isinstance(weakness, list):
            errors.append(f"{label}.weakness must be list")
        else:
            for j, row in enumerate(weakness):
                row_label = f"{label}.weakness[{j}]"
                if not isinstance(row, dict):
                    errors.append(f"{row_label} must be an object")
                    continue
                reject_extra_keys(errors, row, WEAKNESS_KEYS, row_label)
                for key in ("severity", "type", "description"):
                    if not isinstance(row.get(key), str):
                        errors.append(f"{row_label}.{key} must be string")

        if entry.get("decision") not in ALLOWED_DECISIONS:
            errors.append(f"{label}.decision must be continue|stop")

    if isinstance(current_phase, int) and history:
        last_phase = history[-1].get("phase")
        if isinstance(last_phase, int) and last_phase not in {current_phase, current_phase - 1}:
            errors.append(
                f"phase_history drift: last phase={last_phase} does not align with "
                f"state.current_phase={current_phase}"
            )

    if isinstance(current_phase, int) and step_status in {"critic_finished", "improver_finished"}:
        current_entry = entries_by_phase.get(current_phase)
        if current_entry is None:
            errors.append(
                f"step_status={step_status} requires phase_history entry for current phase "
                f"{current_phase}"
            )
        elif step_status == "critic_finished":
            if "scores" not in current_entry:
                errors.append(f"phase_history[{current_phase}] missing scores")
            if not isinstance(current_entry.get("new_traces"), list):
                errors.append(f"phase_history[{current_phase}].new_traces must be list")
            if not isinstance(current_entry.get("weakness"), list):
                errors.append(f"phase_history[{current_phase}].weakness must be list")
            if current_entry.get("decision") not in ALLOWED_DECISIONS:
                errors.append(f"phase_history[{current_phase}].decision must be continue|stop")
        elif step_status == "improver_finished":
            if "improvement" not in current_entry:
                errors.append(f"phase_history[{current_phase}] missing improvement")
            else:
                validate_improvement_list(
                    errors, current_entry.get("improvement"), f"phase_history[{current_phase}]"
                )
            if "scores" not in current_entry:
                errors.append(f"phase_history[{current_phase}] missing scores")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        default="worktraces/refine_state.json",
        help="Path to refine_state.json",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print failures",
    )
    parser.add_argument(
        "--skip-files",
        action="store_true",
        help=(
            "Do not require runner or trace JSON paths to exist on disk "
            "(use for refine_state_template.json or offline scaffold checks)."
        ),
    )
    args = parser.parse_args()

    path = Path(args.path).resolve()
    if not path.exists():
        print(f"BLOCKER: state file not found: {path}")
        return 1

    try:
        raw = load_json(path)
    except json.JSONDecodeError as exc:
        print(f"BLOCKER: invalid JSON: {exc}")
        return 1

    if not isinstance(raw, dict):
        print("BLOCKER: root JSON value must be an object")
        return 1

    state = unwrap_refine_state(raw)

    errors = validate(state, path, skip_files=args.skip_files)
    if errors:
        print("BLOCKER: INVALID refine_state.json. Do not run traces until fixed.")
        for e in errors:
            print(f"- {e}")
        return 1

    if not args.quiet:
        print(f"OK: {path} (pre-trace gate passed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
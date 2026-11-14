"""Synthetic path handoffs; no generated command or validator is run."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "fill_critic_prompt", ROOT / "scaffolds" / "fill_critic_prompt.py"
)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/a workspace/refine_state.json",
        "/tmp/it's $HOME; [state]/refine_state.json",
        r'C:\Users\synthetic name\a "quoted" & folder\refine_state.json',
    ],
)
@pytest.mark.parametrize("phase", ["0", "1"])
def test_state_path_is_one_json_argument(monkeypatch, capsys, path, phase):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fill_critic_prompt.py",
            "--note-md-path",
            "/synthetic note.md",
            "--memory-design-md-path",
            "/synthetic design.md",
            "--implemented-md-path",
            "/synthetic implemented.md",
            "--refine-state-path",
            path,
            "--phase",
            phase,
        ],
    )
    generator.main()
    output = capsys.readouterr().out
    argument = output.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert json.loads(argument) == path
    assert "argument-vector tool" in output
    assert "→" in output


def test_outer_handoff_requires_separate_arguments():
    command = (ROOT / "commands" / "auto-memory-refine.md").read_text(encoding="utf-8")
    section = command.split("## Launching the Critic", 1)[1].split("## Rules", 1)[0]
    assert "argument-vector tool" in section
    for flag in (
        "--note-md-path",
        "--memory-design-md-path",
        "--implemented-md-path",
        "--refine-state-path",
    ):
        assert flag + "\n   {" in section

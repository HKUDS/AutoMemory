"""Normalize ``python -m nanobot`` stdout captured from a non-TTY subprocess."""

from __future__ import annotations

import re

# CSI / OSC sequences (Rich, spinners, cursor moves).
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_CSI = re.compile(r"\x1b\[([0-?]*)([ -/]*)([@-~])")


def _render_terminal_snapshot(raw: str) -> str:
    """Apply the small subset of terminal controls Rich uses while streaming."""
    lines = [""]
    row = 0
    pos = 0
    for match in _CSI.finditer(raw):
        chunk = raw[pos : match.start()]
        for ch in chunk:
            if ch == "\r":
                lines[row] = ""
            elif ch == "\n":
                row += 1
                while row >= len(lines):
                    lines.append("")
            elif ch != "\x1b":
                lines[row] += ch

        params, _intermediate, final = match.groups()
        n = 1
        digits = re.sub(r"\D", "", params)
        if digits:
            n = int(digits)
        if final == "A":
            row = max(0, row - n)
        elif final == "B":
            row += n
            while row >= len(lines):
                lines.append("")
        elif final == "K":
            lines[row] = ""
        pos = match.end()

    for ch in raw[pos:]:
        if ch == "\r":
            lines[row] = ""
        elif ch == "\n":
            row += 1
            while row >= len(lines):
                lines.append("")
        elif ch != "\x1b":
            lines[row] += ch
    return "\n".join(lines)


def _drop_stream_frames(text: str) -> str:
    """Collapse cumulative streaming frames like 'You're' -> 'You're 34 years old.'."""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and out:
            prev = out[-1].strip()
            if stripped == prev:
                continue
            if prev and stripped.startswith(prev):
                out.pop()
        out.append(line)
    return "\n".join(out)


def clean_captured_stdout(raw: str) -> str:
    """Strip ANSI and CLI progress noise; keep assistant text after the last ``🐈 nanobot`` banner."""
    if not raw:
        return ""
    s = _ANSI.sub("", _render_terminal_snapshot(raw)).replace("\r", "")
    lines_out: list[str] = []
    for line in s.splitlines():
        stripped = line.strip()
        if not stripped:
            lines_out.append("")
            continue
        low = stripped.lower()
        if "nanobot is thinking" in low:
            continue
        if stripped[:1] in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏":
            continue
        lines_out.append(line)
    s = "\n".join(lines_out).strip()
    marker = "🐈 nanobot"
    if marker in s:
        s = s.split(marker)[-1].strip()
    s = _drop_stream_frames(s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s

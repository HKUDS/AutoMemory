"""CLI commands for ask_ref — browse and query the reference technique catalog."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="ask_ref",
    help="Browse and query the reference technique catalog.",
    no_args_is_help=True,
)

_json_output: bool = False
_HUB_DIR = Path(__file__).resolve().parent.parent
_REF_DIR = _HUB_DIR.parent
_MAP_FILE = _HUB_DIR / "technique_map.json"


def _load_map() -> dict:
    with open(_MAP_FILE, encoding="utf-8") as f:
        return json.load(f)


def _output(data, text_fn):
    if _json_output:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        text_fn(data)


def _read_lines(filepath: Path, start: int, end: int) -> str:
    """Read lines [start, end] (1-indexed, inclusive) from a file."""
    lines = filepath.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[start - 1 : end])


def _format_card(t: dict) -> str:
    """Format a compact technique card (used by ls <category> and grep)."""
    w = ", ".join(t["philosophy"]["weight"])
    l = ", ".join(t["philosophy"]["logic"])
    lines = [
        f'[{t["id"]}] {t["name"]}',
        f"  Source: {t['source']} | Weight: {w} | Logic: {l}",
        f"  {t['summary']}",
        f"  Tradeoffs: {t['tradeoffs']}",
    ]
    return "\n".join(lines)


@app.callback()
def main(
    json_out: bool = typer.Option(
        False, "--json", help="Output JSON instead of structured text."
    ),
):
    """ask_ref — Browse and query the reference technique catalog."""
    global _json_output
    _json_output = json_out


@app.command("ls")
def ls(
    category: Optional[str] = typer.Argument(
        None, help="Category to list. Omit to show all categories."
    ),
):
    """List techniques by category."""
    catalog = _load_map()
    techniques = catalog["techniques"]
    categories = catalog["categories"]

    by_cat: dict[str, list[dict]] = {}
    for t in techniques:
        by_cat.setdefault(t["category"], []).append(t)

    if category is None:
        data = [
            {"category": cat, "description": categories.get(cat, ""), "count": len(ts), "ids": [t["id"] for t in ts]}
            for cat, ts in by_cat.items()
        ]

        def _text(d):
            for item in d:
                ids = "  ".join(item["ids"])
                print(f'{item["category"]}/ ({item["count"]})')
                print(f"  {ids}")
                print()

        _output(data, _text)
    else:
        if category not in by_cat:
            valid = ", ".join(by_cat.keys())
            print(f"Unknown category '{category}'. Valid: {valid}", file=sys.stderr)
            raise typer.Exit(1)

        ts = by_cat[category]
        data = [_technique_to_dict(t) for t in ts]

        def _text(d):
            print(f"{category}/ ({len(d)})")
            print()
            for t in d:
                print(_format_card(t))
                print()

        _output(data, _text)


@app.command("cat")
def cat_cmd(
    technique_id: str = typer.Argument(..., help="Technique ID to display."),
):
    """Show technique details with spliced reference content."""
    catalog = _load_map()
    technique = None
    for t in catalog["techniques"]:
        if t["id"] == technique_id:
            technique = t
            break

    if technique is None:
        ids = [t["id"] for t in catalog["techniques"]]
        print(f"Unknown technique '{technique_id}'.", file=sys.stderr)
        print(f"Available: {', '.join(ids)}", file=sys.stderr)
        raise typer.Exit(1)

    spliced_sections = []
    for ref in technique.get("detail_refs", []):
        filepath = _REF_DIR / ref["file"]
        if not filepath.exists():
            spliced_sections.append(f"--- {ref['file']} (file not found) ---")
            continue
        start, end = ref["lines"]
        content = _read_lines(filepath, start, end)
        spliced_sections.append(f"--- {ref['file']} (lines {start}-{end}) ---\n{content}")

    prompt_paths = [
        f"reference/{p}" for p in technique.get("prompt_refs", [])
    ]

    data = _technique_to_dict(technique)
    data["detail_content"] = spliced_sections
    data["prompt_refs_full"] = prompt_paths

    def _text(d):
        w = ", ".join(d["philosophy"]["weight"])
        l = ", ".join(d["philosophy"]["logic"])
        print(f'[{d["id"]}] {d["name"]}')
        print(f"Source: {d['source']} | Category: {d['category']}")
        print(f"Weight: {w} | Logic: {l}")
        print()
        print(f"Summary: {d['summary']}")
        print(f"Tradeoffs: {d['tradeoffs']}")
        print(f"When to use: {d['when_to_use']}")
        for section in d["detail_content"]:
            print()
            print(section)
        if d["prompt_refs_full"]:
            print()
            print("See also:")
            for p in d["prompt_refs_full"]:
                print(f"  {p}")

    _output(data, _text)


@app.command("grep")
def grep(
    pattern: str = typer.Argument(
        ...,
        help="Text to search in name/summary/tradeoffs/when_to_use (case-insensitive). "
        "Use a design topic like 'dedup' or 'extraction', NOT a philosophy value.",
    ),
    category: Optional[str] = typer.Option(
        None, "-c", "--category", help="Filter by category."
    ),
    logic: Optional[str] = typer.Option(
        None, "-l", "--logic", help="Filter by logic style (rigorous, intelligent)."
    ),
):
    """Search technique descriptions by keyword, optionally filtered by category or logic style."""
    catalog = _load_map()
    tokens = pattern.lower().split()
    matches = []

    for t in catalog["techniques"]:
        if category and t["category"] != category:
            continue
        if logic and logic not in t["philosophy"]["logic"]:
            continue

        searchable = f"{t['name']} {t['summary']} {t['tradeoffs']} {t['when_to_use']}".lower()
        token_hits = sum(1 for tok in tokens if tok in searchable)
        total_hits = sum(searchable.count(tok) for tok in tokens)
        if token_hits > 0:
            matches.append((token_hits, total_hits, t))

    matches.sort(key=lambda x: (-x[0], -x[1]))
    results = [_technique_to_dict(t) for _, _, t in matches]

    def _text(d):
        print(f'grep "{pattern}" ({len(d)} match{"es" if len(d) != 1 else ""})')
        if not d:
            return
        print()
        for t in d:
            print(_format_card(t))
            print()

    _output(results, _text)


def _technique_to_dict(t: dict) -> dict:
    """Return a clean dict for a technique (passthrough for JSON output)."""
    return {
        "id": t["id"],
        "name": t["name"],
        "source": t["source"],
        "category": t["category"],
        "summary": t["summary"],
        "tradeoffs": t["tradeoffs"],
        "when_to_use": t["when_to_use"],
        "philosophy": t["philosophy"],
        "detail_refs": t.get("detail_refs", []),
        "prompt_refs": t.get("prompt_refs", []),
    }

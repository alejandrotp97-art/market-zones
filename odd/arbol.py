"""Render an ODD feature document's task table as a progress tree.

Usage: python odd/arbol.py <feature-name>   (reads odd/tasks/<feature-name>.md)
"""
import re
import sys
from pathlib import Path

ICONS = {"done": ("✔", "32"), "in-progress": ("◐", "33"), "pending": ("○", "90"), "blocked": ("✖", "31")}


def parse(text: str) -> tuple[str, list[dict]]:
    title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), "feature")
    rows = []
    for ln in text.splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) == 5 and re.fullmatch(r"T\d+", cells[0]):
            rows.append(dict(zip(("id", "specs", "route", "status", "evidence"), cells, strict=True)))
    return title, rows


def render(title: str, rows: list[dict], color: bool = True) -> str:
    paint = (lambda s, c: f"\033[{c}m{s}\033[0m") if color else (lambda s, c: s)
    done = sum(r["status"] == "done" for r in rows)
    width = 20
    filled = round(width * done / len(rows)) if rows else 0
    out = [paint(title, "1"), f"{'█' * filled}{'░' * (width - filled)} {done}/{len(rows)} tareas"]
    for i, r in enumerate(rows):
        icon, c = ICONS.get(r["status"], ("?", "35"))
        branch = "└──" if i == len(rows) - 1 else "├──"
        out.append(f"{branch} {paint(icon, c)} {paint(r['id'], '1')} [{r['specs']}] {r['evidence']}")
    return "\n".join(out)


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "ciclo-economico"
    path = Path(__file__).parent / "tasks" / f"{name}.md"
    print(render(*parse(path.read_text(encoding="utf-8")), color=sys.stdout.isatty()))


if __name__ == "__main__":
    main()

"""Machine-rendered capability coverage block for the technical report."""

from __future__ import annotations

import json
from pathlib import Path

from .sanitize import sanitize_text
from .executor import replace_artifact_text

BEGIN = "<!-- BEGIN MACHINE CAPABILITY COVERAGE -->"
END = "<!-- END MACHINE CAPABILITY COVERAGE -->"


def render(run_dir: str | Path) -> str:
    run = Path(run_dir).expanduser().resolve()
    doc = json.loads((run / "capabilities.json").read_text("utf-8"))
    lines = [BEGIN, "## Deterministic capability coverage", "",
             "| capability | status | reason |", "|---|---|---|"]
    for row in sorted(doc.get("capabilities", []),
                      key=lambda item: item.get("capability_id", "")):
        reason = str(row.get("reason", "")).replace("|", "\\|") or "—"
        lines.append(
            f"| `{row.get('capability_id', '')}` | `{row.get('status', '')}` | {reason} |")
    lines += ["", END, ""]
    return "\n".join(lines)


def write(run_dir: str | Path) -> Path:
    run = Path(run_dir).expanduser().resolve()
    out = run / "coverage-summary.md"
    replace_artifact_text(out, sanitize_text(render(run)))
    return out


def extract(text: str) -> str:
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        return ""
    start = text.find(BEGIN)
    end = text.find(END, start + len(BEGIN)) if start >= 0 else -1
    if start < 0 or end < 0:
        return ""
    return text[start:end + len(END)] + "\n"

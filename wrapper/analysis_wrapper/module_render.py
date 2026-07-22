"""Machine-rendered module table backed by validated module-map.json."""

from __future__ import annotations

from pathlib import Path

from . import module_map
from .executor import replace_artifact_text
from .sanitize import sanitize_text

BEGIN = "<!-- BEGIN MACHINE MODULE MAP -->"
END = "<!-- END MACHINE MODULE MAP -->"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def render(run_dir: str | Path) -> str:
    run = Path(run_dir).expanduser().resolve()
    candidates, mapping = module_map.validate(run)
    owners: dict[str, list[str]] = {}
    for row in mapping["candidate_dispositions"]:
        for module_id in row.get("module_ids", []):
            owners.setdefault(module_id, []).append(row["candidate_id"])
    lines = [BEGIN, "## Modules", "",
             "| module-id | name | classification | confidence | aliases | candidate lineage |",
             "|---|---|---|---|---|---|"]
    for row in sorted(mapping["modules"], key=lambda item: item["module_id"]):
        module_id = row["module_id"]
        aliases = _cell(", ".join(sorted(set(row.get("aliases", []))))) or "—"
        lines.append(
            f"| `{module_id}` | {_cell(row.get('name', module_id))} | "
            f"`{row.get('classification', '')}` | `{row.get('confidence', '')}` | "
            f"{aliases} | {len(owners.get(module_id, []))} candidate(s) |")
    lines += ["", f"Mechanical candidates: {candidates.get('mechanical_candidate_count', candidates.get('candidate_count', 0))}; "
              f"synthesis-added candidates: {candidates.get('additional_candidate_count', 0)}. "
              "Candidate accounting is complete; module discovery is not claimed complete.",
              "", END, ""]
    return "\n".join(lines)


def write(run_dir: str | Path) -> Path:
    run = Path(run_dir).expanduser().resolve()
    out = run / "module-summary.md"
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

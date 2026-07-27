"""Report repair as edit-ops (57B-113 / 57B-117, M3).

The failure mode this module exists to prevent was observed, not theorised:
a model asked to "fix" a document rewrites it, and rewriting is where prose
quietly gets condensed — two sentences become one, a qualifier disappears,
and nothing in the pipeline notices because the document still validates.
The old flow made that inevitable, because a correction WAS a regeneration.

So a repair here is never a rewrite. It is a set of edit operations —
``{locate, replace, fixes}`` — applied mechanically by the wrapper. Three
properties follow, and they are properties of the mechanism rather than of
the model's good behaviour:

* **Unchanged text stays byte-identical.** The model cannot touch a sentence
  it did not name; condensing something outside the error site is impossible,
  not merely discouraged.
* **Every edit must cite the failure it fixes.** An op whose ``fixes`` does
  not name an outstanding validator failure is rejected. That is "only modify
  errors when necessary", enforced rather than requested.
* **No unprompted revision exists.** A repair task is composed ONLY from a
  failure list. There is no polish pass, no tidy pass, no final cleanup — a
  document with zero failures is never handed to a model again.

Only one fix class may legitimately remove content from a document: a prose
overflow, and only by RELOCATING it to the companion document. That op is
policy-marked so the diff guard permits its large word delta, and the
relocation invariant then verifies the content actually landed elsewhere.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..sanitize import sanitize_text
from . import reports, sections as catalog
from .composer import compose
from .engine import Engine
from .validators import apply_edit_ops, relocation_invariant

REPAIR_OUTPUT_SCHEMA_ID = "repair-edit-ops.v1"

# Per-check diff policy. A citation or vocabulary fix touches a few words; an
# overflow fix is expected to move a paragraph and is marked accordingly so
# the guard does not reject the one edit that is SUPPOSED to be large.
DIFF_POLICY: dict[str, dict[str, Any]] = {
    "ceiling-section-overflow": {"requires_relocation": True},
    "ceiling-document-overflow": {"requires_relocation": True},
    "floor-section-thin": {"max_word_delta": 400},
    "floor-required-content": {"max_word_delta": 120},
    "citation-grammar": {"max_word_delta": 20},
    "citation-revision-mismatch": {"max_word_delta": 20},
    "numeric-provenance": {"max_word_delta": 20},
    "forbidden-vocabulary": {"max_word_delta": 30},
}

_PREAMBLE = """\
You are REPAIRING one report section. You are not rewriting or improving it.

Return JSON: {"edits": [{"locate": "<exact text to replace>", "replace":
"<replacement>", "fixes": "<the check id this edit fixes>"}]}.

Rules, all enforced mechanically after you answer:
- Emit an edit ONLY for a listed failure. Every `fixes` value must be one of
  the check ids given below; an edit that fixes nothing on that list is
  rejected outright.
- `locate` must appear EXACTLY ONCE in the section text as given. Copy it
  verbatim, including punctuation and whitespace.
- Touch nothing else. Text you do not name stays byte-identical, and that is
  the point: this section has already been reviewed and must not drift.
- NEVER shorten, condense, merge sentences, or drop a qualifier to make
  something read better. Wordy is acceptable; thinner is not.
- For an overflow failure the ONLY permitted remedy is RELOCATION: move the
  content to the companion document by replacing it with a one-line pointer
  naming exactly what moved and where. Do not delete it.
- If a listed failure cannot be fixed by a local edit, emit no edit for it and
  it will be re-reported honestly rather than silently accepted.
"""


class RepairError(ValueError):
    """A repair precondition failed — no repair is attempted rather than a
    speculative edit applied."""


def repair_task_id(section_id: str) -> str:
    return "repair-" + section_id.replace(".", "-")


def failures_by_section(run_dir: str | Path,
                        documents: Iterable[str] | None = None) -> dict[str, list[dict]]:
    """section_id -> outstanding validator failures, across every document.

    This is the ONLY thing that can trigger a repair task. No failures, no
    task: there is deliberately no path by which a clean section is handed
    back to a model.
    """
    run = Path(run_dir).expanduser().resolve()
    grouped: dict[str, list[dict]] = {}
    for document in (documents or catalog.DOCUMENTS):
        report = reports.document_floors(run, document)
        for failure in report["failures"]:
            location = str(failure.get("location", ""))
            if location in catalog.BY_ID:
                grouped.setdefault(location, []).append(failure)
    return grouped


def plan_repairs(run_dir: str | Path, *,
                 context_budget_tokens: int = 96_000) -> list[str]:
    """Compose one repair task per section that actually has failures."""
    run = Path(run_dir).expanduser().resolve()
    outstanding = failures_by_section(run)
    if not outstanding:
        return []
    bodies = reports.collected_sections(run)
    packets = []
    for section_id, failures in sorted(outstanding.items()):
        section = catalog.BY_ID[section_id]
        if section.kind == "render":
            # A rendered section cannot be repaired by a model: it is a pure
            # function of artifacts, so a failure here means the ARTIFACT or
            # the renderer is wrong. Surfacing that is right; asking a model
            # to patch generated text would hide it.
            continue
        body = bodies.get(section_id)
        if body is None:
            continue
        listed = "\n".join(
            f"- `{failure['check']}`: {failure['detail']}" for failure in failures)
        instructions = "\n".join([
            _PREAMBLE, "",
            f"Section: `{section_id}` ({section.heading}) in `{section.document}`.",
            f"Companion document for any relocation: "
            f"`{catalog.TECHNICAL if section.document == catalog.OVERVIEW else catalog.OVERVIEW}`.",
            "", "Outstanding failures — the complete list, fix each with a local edit:",
            listed, "",
            "Valid `fixes` values: " + ", ".join(
                sorted({str(failure["check"]) for failure in failures})),
        ]) + "\n"
        packets.extend(compose(
            task_id=repair_task_id(section_id),
            template_id=f"repair-{section_id}",
            template_version="repair-1+" + str(len(failures)),
            task_type="repair-edit-ops", instructions=instructions,
            inputs={"section.md": sanitize_text(body),
                    "failures.json": json.dumps(failures, indent=1, sort_keys=True)},
            output_schema_id=REPAIR_OUTPUT_SCHEMA_ID,
            context_budget_tokens=context_budget_tokens))
    if not packets:
        return []
    Engine(run).create_tasks(packets)
    return [packet.task_id for packet in packets]


def apply_repairs(run_dir: str | Path) -> dict[str, Any]:
    """Apply every validated repair output to its section, mechanically.

    Returns a per-section record of what was applied and what was rejected.
    A rejected op is reported, never quietly dropped: a repairer that tried to
    edit outside its mandate is a signal worth seeing.
    """
    from .results import validated_outputs

    run = Path(run_dir).expanduser().resolve()
    outstanding = failures_by_section(run)
    bodies = reports.collected_sections(run)
    outputs = validated_outputs(run, task_type="repair-edit-ops")

    applied: dict[str, Any] = {}
    for task_id, output in sorted(outputs.items()):
        section_id = task_id[len("repair-"):].replace("-", ".", 1)
        # task ids flatten dots; resolve against the catalog rather than guess.
        match = next((sid for sid in catalog.BY_ID if repair_task_id(sid) == task_id), None)
        section_id = match or section_id
        body = bodies.get(section_id)
        if body is None or not isinstance(output, dict):
            continue
        failures = outstanding.get(section_id, [])
        allowed = {str(failure["check"]) for failure in failures}
        new_body, rejected = apply_edit_ops(
            body, output.get("edits", []), allowed, policy=DIFF_POLICY)
        record: dict[str, Any] = {
            "section_id": section_id,
            "edits_offered": len(output.get("edits", []) or []),
            "edits_rejected": rejected,
            "changed": new_body != body,
        }
        if new_body != body:
            companion_id = (catalog.TECHNICAL
                            if catalog.BY_ID[section_id].document == catalog.OVERVIEW
                            else catalog.OVERVIEW)
            companion_path = run / companion_id
            companion = companion_path.read_text("utf-8") if companion_path.is_file() else ""
            record["relocation_failures"] = relocation_invariant(body, new_body, companion)
            record["body"] = new_body
        applied[section_id] = record
    return applied

"""Shared, detector-aware data-model capability classification.

Extraction completion and source-universe detection are separate facts.  A
producer that does not understand a detected datastore family must never turn a
zero-result scan into ``not-applicable``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DataModelCoverage:
    status: str
    detected_families: tuple[str, ...]
    supported_families: tuple[str, ...]
    extracted_families: tuple[str, ...]
    unresolved_families: tuple[str, ...]
    detector_complete: bool
    data_store_count: int
    unresolved_bindings: int
    details: tuple[dict, ...]
    notes: tuple[str, ...]


def classify(repos: list[dict]) -> DataModelCoverage:
    details: list[dict] = []
    detected_all: set[str] = set()
    supported_all: set[str] = set()
    extracted_all: set[str] = set()
    unresolved_all: set[str] = set()
    notes: list[str] = []
    store_count = 0
    binding_count = 0
    detector_complete = bool(repos)
    any_partial = False
    any_failed = False

    for block in repos:
        evidence = block.get("table_evidence", {})
        detector = evidence.get("detector_coverage")
        tables = evidence.get("tables", {})
        count = len(tables) if isinstance(tables, dict) else 0
        store_count += count
        binding_count += len(evidence.get("unresolved", []))

        if not isinstance(detector, dict):
            complete = False
            detected = set()
            supported = set()
            extracted = set()
            unsupported = set()
            notes.append(f"{block.get('repo_id', '')}: detector metadata unavailable")
        else:
            complete = bool(detector.get("complete"))
            detected = {str(v) for v in detector.get("detected_families", [])}
            supported = {str(v) for v in detector.get("supported_families", [])}
            extracted = {str(v) for v in detector.get("extracted_families", [])}
            unsupported = {str(v) for v in detector.get("unsupported_families", [])}

        unresolved = unsupported | (supported - extracted)
        detected_all.update(detected)
        supported_all.update(supported)
        extracted_all.update(extracted)
        unresolved_all.update(unresolved)
        detector_complete = detector_complete and complete

        ast_required = bool(supported & {"gorm", "sequelize"})
        sql_required = "sql" in supported
        sql = evidence.get("sql_coverage", {})
        producer_failed = ((ast_required and not evidence.get("available")) or
                           (sql_required and not sql.get("available")))
        producer_partial = (sql_required and sql.get("available") and
                            not sql.get("complete", False))
        cap_hit = any("COVERAGE CAP" in str(note)
                      for note in evidence.get("notes", []))
        any_failed = any_failed or producer_failed
        any_partial = any_partial or producer_partial or cap_hit or bool(unresolved)

        if not complete:
            repo_status = "unavailable" if not count else "partial"
        elif not detected:
            repo_status = "not-applicable"
        elif producer_failed and not extracted:
            repo_status = "unavailable"
        elif count and not (producer_failed or producer_partial or cap_hit or unresolved):
            repo_status = "complete"
        elif count or extracted:
            repo_status = "partial"
        else:
            repo_status = "unavailable"
        details.append({
            "repo_id": block.get("repo_id", ""),
            "status": repo_status,
            "detector_complete": complete,
            "detected_families": sorted(detected),
            "supported_families": sorted(supported),
            "extracted_families": sorted(extracted),
            "unresolved_families": sorted(unresolved),
            "data_store_count": count,
        })

    if not repos:
        status = "not-applicable"
    elif not detector_complete:
        status = "partial" if store_count else "unavailable"
    elif not detected_all:
        status = "not-applicable"
    elif store_count and not (any_failed or any_partial or unresolved_all):
        status = "complete"
    elif store_count or extracted_all:
        status = "partial"
    else:
        status = "unavailable"

    if unresolved_all:
        notes.append("Detected datastore families without complete extraction: "
                     + ", ".join(sorted(unresolved_all)))
    if not detector_complete:
        notes.append("Datastore-family detection did not complete for every target.")

    return DataModelCoverage(
        status=status,
        detected_families=tuple(sorted(detected_all)),
        supported_families=tuple(sorted(supported_all)),
        extracted_families=tuple(sorted(extracted_all)),
        unresolved_families=tuple(sorted(unresolved_all)),
        detector_complete=detector_complete,
        data_store_count=store_count,
        unresolved_bindings=binding_count,
        details=tuple(sorted(details, key=lambda row: str(row.get("repo_id", "")))),
        notes=tuple(notes),
    )

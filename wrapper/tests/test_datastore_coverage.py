"""Detector-aware datastore capability states (57B-68)."""

from analysis_wrapper.datastore_coverage import classify


def _repo(*, detected=(), supported=(), extracted=(), tables=None,
          complete=True, available=True, sql_available=True, sql_complete=True):
    return {
        "repository_ref": "sample",
        "table_evidence": {
            "available": available,
            "tables": tables or {},
            "unresolved": [],
            "notes": [],
            "sql_coverage": {"available": sql_available, "complete": sql_complete},
            "detector_coverage": {
                "complete": complete,
                "detected_families": list(detected),
                "supported_families": list(supported),
                "unsupported_families": sorted(set(detected) - set(supported)),
                "extracted_families": list(extracted),
            },
        },
    }


def test_complete_detector_with_no_signal_is_not_applicable():
    assert classify([_repo()]).status == "not-applicable"


def test_supported_extracted_family_is_complete():
    row = _repo(detected=("sql",), supported=("sql",), extracted=("sql",),
                tables={"widgets": {"read": ["query.sql"]}})
    assert classify([row]).status == "complete"


def test_recognized_unsupported_family_is_unavailable_not_not_applicable():
    result = classify([_repo(detected=("document-db",))])
    assert result.status == "unavailable"
    assert result.unresolved_families == ("document-db",)


def test_mixed_extracted_and_unsupported_families_is_partial():
    row = _repo(detected=("sql", "document-db"), supported=("sql",),
                extracted=("sql",), tables={"widgets": {"read": ["query.sql"]}})
    result = classify([row])
    assert result.status == "partial"
    assert result.unresolved_families == ("document-db",)


def test_missing_or_failed_detector_fails_closed():
    assert classify([_repo(complete=False)]).status == "unavailable"
    legacy = {"repository_ref": "legacy", "table_evidence": {"available": True,
              "tables": {}, "unresolved": []}}
    assert classify([legacy]).status == "unavailable"


def test_detected_supported_family_with_failed_producer_is_unavailable():
    row = _repo(detected=("sequelize",), supported=("sequelize",),
                available=False)
    assert classify([row]).status == "unavailable"


def test_unresolved_binding_degrades_extracted_model_to_partial():
    row = _repo(detected=("sql",), supported=("sql",), extracted=("sql",),
                tables={"widgets": {"read": ["query.sql"]}})
    row["table_evidence"]["unresolved"] = [{"kind": "dynamic-name"}]
    assert classify([row]).status == "partial"

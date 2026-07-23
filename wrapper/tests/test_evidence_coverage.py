"""57B-79: the canonical two-axis Coverage type and its legacy adapters."""

import json

import pytest

from analysis_wrapper.datastore_coverage import DataModelCoverage
from analysis_wrapper.evidence.coverage import (
    APPLICABILITY_VALUES,
    STATUS_VALUES,
    Coverage,
    aggregate,
    from_capability_status,
    from_datastore_coverage,
    from_signal_status,
)


def test_not_applicable_requires_positive_detection_evidence():
    with pytest.raises(ValueError, match="detail"):
        Coverage(applicability="not-applicable", status="complete",
                 reason_code="no-datastore-signal", detail="")
    with pytest.raises(ValueError, match="detail"):
        Coverage(applicability="not-applicable", status="complete",
                 reason_code="no-datastore-signal", detail="   ")
    coverage = Coverage(
        applicability="not-applicable", status="complete",
        reason_code="no-datastore-signal",
        detail="complete detector scan observed no datastore-family signals")
    assert coverage.applicability == "not-applicable"


def test_detected_but_unsupported_maps_to_unavailable_not_not_applicable():
    coverage = Coverage(
        applicability="applicable", status="unavailable",
        reason_code="unsupported-family",
        detail="detected an ORM family this provider does not parse")
    assert coverage.applicability == "applicable"
    assert coverage.status == "unavailable"


def test_legitimate_empty_applicable_result_is_representable_as_complete():
    coverage = Coverage(applicability="applicable", status="complete",
                        reason_code="zero-matches",
                        detail="scan completed; no matches found")
    assert coverage.status == "complete"


def test_unknown_values_are_rejected():
    with pytest.raises(ValueError, match="applicability"):
        Coverage(applicability="maybe", status="complete", reason_code="x")
    with pytest.raises(ValueError, match="status"):
        Coverage(applicability="applicable", status="bogus", reason_code="x")
    with pytest.raises(ValueError, match="reason_code"):
        Coverage(applicability="applicable", status="complete", reason_code="")
    with pytest.raises(ValueError, match="reason_code"):
        Coverage(applicability="applicable", status="complete", reason_code="not valid!")


def test_coverage_is_frozen_and_json_safe():
    coverage = Coverage(applicability="applicable", status="complete",
                        reason_code="ok")
    with pytest.raises(Exception):
        coverage.status = "failed"
    assert json.dumps(coverage.to_dict(), sort_keys=True)
    assert coverage.to_dict() == {
        "applicability": "applicable", "status": "complete",
        "reason_code": "ok", "detail": "",
    }


def test_aggregate_reports_worst_status_without_letting_complete_mask_it():
    complete = Coverage(applicability="applicable", status="complete", reason_code="js-ok")
    unavailable = Coverage(applicability="applicable", status="unavailable",
                           reason_code="go-unsupported",
                           detail="analyzer binary missing")
    result = aggregate([complete, unavailable])
    assert result.status == "unavailable"
    assert result.applicability == "applicable"
    # Order must not matter.
    assert aggregate([unavailable, complete]) == result


def test_aggregate_excludes_not_applicable_unless_all_are_not_applicable():
    not_applicable = Coverage(applicability="not-applicable", status="complete",
                              reason_code="no-ui", detail="no frontend facet detected")
    partial = Coverage(applicability="applicable", status="partial",
                       reason_code="cap-hit", detail="evidence capped")
    combined = aggregate([not_applicable, partial])
    assert combined.status == "partial"
    assert combined.applicability == "applicable"

    only_not_applicable = aggregate([not_applicable, not_applicable])
    assert only_not_applicable.applicability == "not-applicable"
    assert only_not_applicable.status == "complete"


def test_aggregate_treats_skipped_and_unavailable_as_worse_than_complete():
    complete = Coverage(applicability="applicable", status="complete", reason_code="ok")
    skipped = Coverage(applicability="applicable", status="skipped",
                       reason_code="tool-missing", detail="analyzer not installed")
    assert aggregate([complete, skipped]).status == "skipped"


def test_aggregate_is_deterministic_regardless_of_input_order():
    a = Coverage(applicability="applicable", status="failed", reason_code="a")
    b = Coverage(applicability="applicable", status="failed", reason_code="b")
    assert aggregate([a, b]) == aggregate([b, a])


def test_aggregate_rejects_empty_input():
    with pytest.raises(ValueError, match="empty"):
        aggregate([])


def test_aggregate_rejects_non_coverage_values():
    with pytest.raises(ValueError, match="Coverage"):
        aggregate([{"status": "complete"}])


def test_legacy_signal_status_adapter_is_total():
    for value in ("complete", "partial", "failed", "skipped"):
        coverage = from_signal_status(value)
        assert coverage.applicability in APPLICABILITY_VALUES
        assert coverage.status in STATUS_VALUES
        assert coverage.status == value
    with pytest.raises(ValueError):
        from_signal_status("bogus")


def test_legacy_capability_status_adapter_is_total():
    for status in ("complete", "partial", "unavailable", "failed"):
        coverage = from_capability_status(status, True)
        assert coverage.applicability == "applicable"
        assert coverage.status == status
    not_applicable = from_capability_status("not-applicable", False)
    assert not_applicable.applicability == "not-applicable"
    with pytest.raises(ValueError):
        from_capability_status("bogus", True)


def test_legacy_capability_status_adapter_rejects_contradiction():
    with pytest.raises(ValueError, match="inconsistent"):
        from_capability_status("not-applicable", True)


def test_legacy_datastore_coverage_adapter_maps_every_status():
    for status in ("complete", "partial", "unavailable", "not-applicable"):
        legacy = DataModelCoverage(
            status=status, detected_families=(), supported_families=(),
            extracted_families=(), unresolved_families=(), detector_complete=True,
            data_store_count=0, unresolved_bindings=0, details=(), notes=("a note",))
        coverage = from_datastore_coverage(legacy)
        assert coverage.applicability in APPLICABILITY_VALUES
        assert coverage.status in STATUS_VALUES
        if status == "not-applicable":
            assert coverage.applicability == "not-applicable"
        else:
            assert coverage.applicability == "applicable"
            assert coverage.status == status

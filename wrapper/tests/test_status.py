from analysis_wrapper.status import Status, aggregate, severity, wrapper_exit_code


def test_severity_order_is_failed_partial_skipped_complete():
    assert (
        severity(Status.FAILED)
        > severity(Status.PARTIAL)
        > severity(Status.SKIPPED)
        > severity(Status.COMPLETE)
    )


def test_aggregate_is_worst_present():
    assert aggregate([Status.COMPLETE, Status.SKIPPED]) is Status.SKIPPED
    assert aggregate([Status.SKIPPED, Status.PARTIAL]) is Status.PARTIAL
    assert aggregate([Status.PARTIAL, Status.FAILED, Status.COMPLETE]) is Status.FAILED


def test_empty_aggregation_fails_closed():
    """Zero signals must never look successful ('nothing ran' is a failure)."""
    assert aggregate([]) is Status.FAILED
    assert wrapper_exit_code([]) == 3


def test_exit_code_nonzero_only_on_failed():
    assert wrapper_exit_code([Status.COMPLETE, Status.PARTIAL, Status.SKIPPED]) == 0
    assert wrapper_exit_code([Status.COMPLETE, Status.FAILED]) == 3

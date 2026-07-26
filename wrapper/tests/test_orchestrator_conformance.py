"""Conformance fixture tests (57B-113 / 57B-115, M1): the golden self-check
(no network) and a live-executor conformance run driven by a fake transport
(still no real network calls)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper.orchestrator.conformance import FIXTURES, run_conformance
from analysis_wrapper.orchestrator.contracts import TASK_TYPES
from analysis_wrapper.orchestrator.executor_api import AdapterConfig


def test_one_fixture_exists_per_task_type():
    assert {fixture.task_type for fixture in FIXTURES} == TASK_TYPES
    assert len({fixture.packet.task_id for fixture in FIXTURES}) == len(FIXTURES)


def test_golden_self_check_passes_every_fixture(tmp_path):
    report = run_conformance(run_dir=str(tmp_path))
    assert report["passed"] is True
    assert set(report["results"]) == TASK_TYPES
    assert all(status == "validated" for status in report["results"].values())


def test_golden_self_check_uses_a_temp_dir_and_cleans_up_when_none_given():
    report = run_conformance()
    assert report["passed"] is True


def test_conformance_with_a_live_executor_uses_the_fixture_dag(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    golden_by_task_id = {fixture.packet.task_id: fixture.golden_output for fixture in FIXTURES}

    def transport(url, headers, body, timeout):
        request = json.loads(body)
        user_content = request["messages"][0]["content"]
        # The fixture's own instructions are the system prompt; recover which
        # fixture this is from the packet's own task_id embedded in the
        # request payload is not directly available, so match on schema id.
        for fixture in FIXTURES:
            if fixture.packet.output_schema_id in user_content:
                output = golden_by_task_id[fixture.packet.task_id]
                break
        else:
            raise AssertionError("no fixture matched this request")
        return 200, json.dumps({
            "content": [{"type": "text", "text": json.dumps(output)}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }).encode("utf-8")

    report = run_conformance(
        run_dir=str(tmp_path),
        config=AdapterConfig(name="anthropic", model="claude-x"),
        concurrency=4, transport=transport)
    assert report["passed"] is True
    assert all(status == "validated" for status in report["results"].values())

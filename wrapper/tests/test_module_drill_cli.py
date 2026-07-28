"""Public Module Drill CLI lifecycle coverage for 57B-136."""

from __future__ import annotations

import json
from pathlib import Path

from analysis_wrapper.cli import main
from analysis_wrapper.module_drill.runtime import initialize_from_overview
from analysis_wrapper.orchestrator.contracts import (
    ExecutorInfo,
    TaskPacket,
    TaskResult,
    TaskTiming,
    ValidationOutcome,
)
from analysis_wrapper.orchestrator.engine import now_iso
from test_module_drill_runtime import _prepared_overview


def test_cli_drives_overview_backed_module_task_lifecycle(tmp_path, capsys):
    overview = _prepared_overview(tmp_path, {"src/create.ts": "export const create = () => true;\n"})
    expected = initialize_from_overview(
        overview, output_root=tmp_path / "output", project_key="workspace",
        selector="create", language="en", run_label="expected")
    # Use the public CLI for a separate, actual run; the direct call only
    # proves the overview fixture itself is suitable as a source.
    assert main([
        "module-init", "--from-overview", str(overview), "--output-root", str(tmp_path / "output"),
        "--project-key", "workspace", "--selector", "create", "--language", "en", "--run-id", "cli",
    ]) == 0
    initialized = json.loads(capsys.readouterr().out)
    run = Path(initialized["run"])
    assert run != expected.run_dir

    packet = TaskPacket.create(
        task_id="candidate-ranking", task_type="module-candidate-ranking",
        template_id="test", template_version="v1", instructions="Select candidate.",
        inputs={"candidates": "candidate-a"}, output_schema_id="module-candidate-ranking/v1",
        context_budget_tokens=300)
    packet_path = tmp_path / "packets.json"
    packet_path.write_text(json.dumps([packet.to_dict()]), encoding="utf-8")
    assert main(["module-register", "--run", str(run), "--packets", str(packet_path)]) == 0
    assert json.loads(capsys.readouterr().out)["created"] == ["candidate-ranking"]
    assert main(["module-next", "--run", str(run), "--executor-kind", "test", "--model", "test-model"]) == 0
    claimed = json.loads(capsys.readouterr().out)[0]
    assert claimed["task"]["task_id"] == "candidate-ranking"
    at = now_iso()
    result = TaskResult(
        task_id="candidate-ranking", status="ok",
        output={
            "decision": "selected", "candidate_ids": ["candidate-a"],
            "selected_candidate_id": "candidate-a", "reason_code": "clear-dominant",
        },
        executor=ExecutorInfo(kind="test", model="test-model", params={}),
        timing=TaskTiming(started_at=at, finished_at=at, wall_clock_s=0.0),
        tokens=None, validation=ValidationOutcome(passed=True, failures=()),
        attempt=claimed["attempt"],
    )
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result.to_dict()), encoding="utf-8")
    assert main(["module-submit", "--run", str(run), "--task", "candidate-ranking",
                 "--result", str(result_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "validated"
    assert main(["module-status", "--run", str(run)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["task_states"] == {"candidate-ranking": "validated"}

    requests = tmp_path / "requests.json"
    requests.write_text(json.dumps([{
        "span_id": "create", "kind": "declaration",
        "ref": "service@NON-GIT:src/create.ts:1", "purpose": "read entry",
    }]), encoding="utf-8")
    assert main(["module-fetch-spans", "--run", str(run), "--requests", str(requests)]) == 0
    assert Path(json.loads(capsys.readouterr().out)["spans"]).is_file()

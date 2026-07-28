"""Revision-checked semantic source-span fetch tests for 57B-136."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis_wrapper.module_drill.runtime import initialize_from_overview
from analysis_wrapper.module_drill.spans import fetch
from analysis_wrapper.module_drill.validation import ContractError
from test_module_drill_runtime import _prepared_overview


def _module_run(tmp_path: Path, source: str) -> tuple[Path, str]:
    overview = _prepared_overview(tmp_path, {"src/handler.ts": source})
    initialized = initialize_from_overview(
        overview, output_root=tmp_path / "module-output", project_key="workspace",
        selector="create record", language="en", run_label="span-test")
    return initialized.run_dir, "service@NON-GIT:src/handler.ts"


def _request(ref: str, line: int, *, kind: str = "handler") -> dict[str, str]:
    return {"span_id": "create-handler", "kind": kind, "ref": f"{ref}:{line}",
            "purpose": "recover the handler behavior"}


def test_fetches_complete_lexical_handler_block_with_exact_range_refs(tmp_path):
    run, ref = _module_run(tmp_path, """// braces in comments { are ignored
export function createRecord() {
  const value = \"not a } boundary\";
  return value;
}

const unrelated = true;
""")

    result = fetch(run, [_request(ref, 2)])
    rows = json.loads(result.read_text(encoding="utf-8"))

    assert rows[0]["status"] == "fetched"
    assert rows[0]["boundary"] == "brace-block"
    assert rows[0]["start_ref"] == f"{ref}:2"
    assert rows[0]["end_ref"] == f"{ref}:5"
    assert "createRecord" in rows[0]["content"]
    assert "unrelated" not in rows[0]["content"]
    assert len(rows[0]["content_sha256"]) == 64


def test_fetches_complete_unbraced_declaration_as_statement(tmp_path):
    run, ref = _module_run(tmp_path, """const handler = () => send(\"created\");
const other = 1;
""")

    result = fetch(run, [_request(ref, 1, kind="declaration")])
    row = json.loads(result.read_text(encoding="utf-8"))[0]

    assert row["status"] == "fetched"
    assert row["boundary"] == "statement"
    assert row["content"] == 'const handler = () => send("created");'
    assert row["start_ref"] == row["end_ref"] == f"{ref}:1"


def test_redacts_secret_shaped_source_values_before_persisting_span(tmp_path):
    run, ref = _module_run(tmp_path, """export function create() {
  const API_TOKEN = \"very-secret-value\";
  return API_TOKEN;
}
""")

    row = json.loads(fetch(run, [_request(ref, 1)]).read_text(encoding="utf-8"))[0]

    assert row["status"] == "fetched"
    assert "very-secret-value" not in row["content"]
    assert "<REDACTED>" in row["content"]


def test_refuses_stale_source_and_output_escape(tmp_path):
    run, ref = _module_run(tmp_path, "export function create() { return true; }\n")
    with pytest.raises(ContractError, match="inside the module run"):
        fetch(run, [_request(ref, 1)], out=tmp_path / "outside.json")

    source = tmp_path / "workspace" / "service" / "src" / "handler.ts"
    source.write_text("export function changed() { return false; }\n", encoding="utf-8")
    with pytest.raises(ContractError, match="source snapshot is stale"):
        fetch(run, [_request(ref, 1)])


def test_discloses_unresolved_boundary_and_never_overwrites_checkpoint(tmp_path):
    run, ref = _module_run(tmp_path, "value\n")
    result = fetch(run, [_request(ref, 1, kind="config-block")])
    row = json.loads(result.read_text(encoding="utf-8"))[0]
    assert row["status"] == "unresolved"
    assert row["content"] == ""
    with pytest.raises(FileExistsError):
        fetch(run, [_request(ref, 1, kind="config-block")])

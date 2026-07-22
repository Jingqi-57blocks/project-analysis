"""system_model.from_go_imports — go list maps into dependency (not call) edges."""

import json
from pathlib import Path

from analysis_wrapper import identity
from analysis_wrapper.system_model import from_go_imports
from analysis_wrapper.system_model.builder import ModelBuilder
from analysis_wrapper.targetspec import RepoTarget, TargetSpec, stable_repo_id

_MODULE = "example.com/app"
_REPO = "app-1"
_REF = "app"
_HEAD = "d" * 40


def _write_golist(run: Path) -> None:
    (run / "imports").mkdir(parents=True, exist_ok=True)
    payload = {
        "module": _MODULE,
        "packages": [
            {"import_path": _MODULE,
             "imports": ["example.com/app/internal/store", "fmt",
                         "github.com/lib/pq"]},
            {"import_path": "example.com/app/internal/store",
             "imports": ["errors", "example.com/app/internal/util"]},
            {"import_path": "example.com/app/internal/util", "imports": ["fmt"]},
        ],
        "stdlib": ["errors", "fmt"],
    }
    (run / "imports" / f"{_REF}.golist.json").write_text(
        json.dumps(payload, sort_keys=True), "utf-8")


def _identities(tmp_path: Path):
    repo = tmp_path / _REF
    repo.mkdir(exist_ok=True)
    return identity.build(
        TargetSpec([RepoTarget(repo_id=_REPO, path=str(repo))]),
        workspace_root=tmp_path, project_id=stable_repo_id(str(tmp_path)))


def _load(tmp_path: Path):
    _write_golist(tmp_path)
    builder = ModelBuilder()
    summary = from_go_imports.load(
        builder, tmp_path, {_REF: _HEAD}, _identities(tmp_path))
    builder.resolve()
    return builder, summary


def test_internal_package_edges_are_observed_dependencies(tmp_path):
    builder, summary = _load(tmp_path)
    deps = [e for e in builder.edges if e.type == "dependency"]
    observed = [e for e in deps if e.status == "observed"]
    # 2 internal edges: app -> internal/store, internal/store -> internal/util.
    assert len(observed) == 2
    assert summary["edges"] == 2
    assert all(e.producer == "go-list" for e in observed)
    # Package granularity is disclosed on every internal dependency edge.
    assert all(e.attrs.get("granularity") == "package" for e in observed)


def test_third_party_import_is_unresolved_not_dropped(tmp_path):
    builder, summary = _load(tmp_path)
    unresolved = [e for e in builder.edges
                  if e.type == "dependency" and e.status == "unresolved"]
    assert len(unresolved) == 1                       # github.com/lib/pq
    assert summary["unresolved"] == 1
    assert unresolved[0].unresolved_target == {"specifier": "github.com/lib/pq"}


def test_stdlib_imports_counted_but_emit_no_edge(tmp_path):
    _builder, summary = _load(tmp_path)
    # fmt (x2) + errors (x1) are stdlib -> counted, never an edge.
    assert summary["stdlib_omitted"] == 3


def test_dependency_lanes_never_emit_a_call_edge(tmp_path):
    builder, _summary = _load(tmp_path)
    # The dependency lanes NEVER produce a `call` edge (57B-30 separation).
    assert not [e for e in builder.edges
                if e.producer in ("go-list", "dependency-cruiser")
                and e.type == "call"]
    # The go-list lane emits ONLY dependency edges (+ structural containment from
    # note_file) — its relationship edges are all `dependency`, never `call`.
    go_types = {e.type for e in builder.edges if e.producer == "go-list"}
    assert "call" not in go_types
    assert go_types <= {"dependency", "containment"}
    relationship = [e for e in builder.edges
                    if e.producer == "go-list" and e.type != "containment"]
    assert relationship and all(e.type == "dependency" for e in relationship)


def test_dotless_third_party_import_is_unresolved_not_dropped(tmp_path):
    # M1 regression: a dotless import NOT in the stdlib set (e.g. a local-replace
    # or vanity path) must be a third-party UNRESOLVED edge, never silently
    # dropped as stdlib. Classification is exact stdlib-set membership.
    (tmp_path / "imports").mkdir(parents=True, exist_ok=True)
    payload = {
        "module": _MODULE,
        "packages": [
            {"import_path": _MODULE,
             "imports": ["fmt", "vanitypkg", "example.com/app/internal/util"]},
            {"import_path": "example.com/app/internal/util", "imports": []},
        ],
        "stdlib": ["fmt"],                            # 'vanitypkg' is dotless but NOT stdlib
    }
    (tmp_path / "imports" / f"{_REF}.golist.json").write_text(
        json.dumps(payload, sort_keys=True), "utf-8")
    builder = ModelBuilder()
    summary = from_go_imports.load(
        builder, tmp_path, {_REF: _HEAD}, _identities(tmp_path))
    builder.resolve()
    unresolved = [e for e in builder.edges
                  if e.type == "dependency" and e.status == "unresolved"]
    assert any(e.unresolved_target == {"specifier": "vanitypkg"} for e in unresolved)
    assert summary["unresolved"] == 1                 # vanitypkg preserved
    assert summary["stdlib_omitted"] == 1             # only fmt counted as stdlib


def test_package_nodes_carry_repo_relative_citations(tmp_path):
    builder, _summary = _load(tmp_path)
    files = [n for n in builder.nodes if n.kind == "file"]
    labels = {n.label for n in files}
    assert "." in labels                              # the module's main package
    assert "internal/store" in labels
    for node in files:
        for cite in node.evidence:
            assert cite.startswith(f"{_REF}@{_HEAD}:")
            assert "/Users/" not in cite


def test_absent_imports_dir_is_disclosed_not_fabricated(tmp_path):
    builder = ModelBuilder()
    summary = from_go_imports.load(
        builder, tmp_path, {}, _identities(tmp_path))
    assert summary["present"] is False
    assert summary["edges"] == 0
    assert not [e for e in builder.edges if e.type == "dependency"]

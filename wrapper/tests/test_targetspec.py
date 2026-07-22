import pytest
from pathlib import Path

from analysis_wrapper.targetspec import (
    GitProvenance,
    IntegrationCandidate,
    PackageManager,
    RepoTarget,
    SCHEMA_VERSION,
    TargetSpec,
    TechnologyFacet,
    stable_repo_id,
)


def _spec() -> TargetSpec:
    return TargetSpec(
        repos=[
            RepoTarget(
                repo_id="api-11112222",
                path="/tmp/w1/api",
                facets=[TechnologyFacet(
                    profile_id="language.javascript", kind="language",
                    scope_roots=["src"], evidence=["src/index.js"],
                )],
                analysis_roots=["src"],
                tier2_exclusions=["docs"],
                pm=PackageManager("yarn", "yarn.lock", "packageManager field declares yarn"),
                git=GitProvenance(head="a" * 40, branch="main", commit_count=5),
            )
        ],
        integration_candidates=[IntegrationCandidate(
            candidate_id="import-1", repo_id="api-11112222", signal_kind="import",
            value="example.invalid/sdk", evidence=["src/client.ts:3"],
        )],
        produced_by="test/0",
        produced_at="2026-07-16T00:00:00Z",
    )


def test_json_round_trip_is_lossless(tmp_path):
    spec = _spec()
    f = tmp_path / "targets.json"
    spec.save(f)
    loaded = TargetSpec.load(f)
    assert loaded == spec


def test_shipped_targetspec_fixture_round_trips_byte_exactly(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "targets.json"
    spec = TargetSpec.load(fixture)
    out = tmp_path / "targets.json"
    spec.save(out)
    assert out.read_bytes() == fixture.read_bytes()


def test_repo_ids_stable_and_collision_free(tmp_path):
    a = tmp_path / "team-a" / "api"
    b = tmp_path / "team-b" / "api"
    a.mkdir(parents=True), b.mkdir(parents=True)
    ida, idb = stable_repo_id(str(a)), stable_repo_id(str(b))
    assert ida != idb, "two repos named 'api' must not collide"
    assert ida.startswith("api-") and idb.startswith("api-")
    assert ida == stable_repo_id(str(a)), "id must be deterministic"


def test_malformed_input_raises_precise_errors():
    with pytest.raises(ValueError, match="'repos'"):
        TargetSpec.from_dict({"nope": []})
    with pytest.raises(ValueError, match="schema_version"):
        TargetSpec.from_dict({"repos": []})
    with pytest.raises(ValueError, match="unsupported fields"):
        TargetSpec.from_dict({"schema_version": SCHEMA_VERSION, "repos": [{
            "repo_id": "x", "path": "/x", "stacks": ["js"],
        }]})
    with pytest.raises(ValueError, match=r"repos\[0\].*repo_id"):
        TargetSpec.from_dict({"schema_version": SCHEMA_VERSION,
                              "repos": [{"path": "/x"}]})
    with pytest.raises(ValueError, match="must be a list"):
        TargetSpec.from_dict({"schema_version": SCHEMA_VERSION, "repos": "nope"})
    with pytest.raises(ValueError, match="duplicate repo_id"):
        TargetSpec.from_dict({"schema_version": SCHEMA_VERSION, "repos": [
            {"repo_id": "x", "path": "/x"}, {"repo_id": "x", "path": "/y"},
        ]})
    with pytest.raises(ValueError, match="unsupported"):
        TargetSpec.from_dict({"schema_version": SCHEMA_VERSION,
                              "repos": [{"repo_id": "x", "path": "/x",
                                           "pm": {"name": "unknown"}}]})
    with pytest.raises(ValueError, match="unknown repo_id"):
        TargetSpec.from_dict({"schema_version": SCHEMA_VERSION,
                              "repos": [{"repo_id": "x", "path": "/x"}],
                              "integration_candidates": [{
                                  "candidate_id": "c", "repo_id": "y",
                                  "signal_kind": "import", "value": "sdk",
                              }]})
    with pytest.raises(ValueError, match="relative"):
        TargetSpec.from_dict({"schema_version": SCHEMA_VERSION, "repos": [{
            "repo_id": "x", "path": "/x", "analysis_roots": ["../outside"],
        }]})
    with pytest.raises(ValueError, match="basename"):
        TargetSpec.from_dict({"schema_version": SCHEMA_VERSION, "repos": [{
            "repo_id": "x", "path": "/x", "pm": {"lockfile": "nested/lock.json"},
        }]})
    with pytest.raises(ValueError, match="40-character SHA"):
        TargetSpec.from_dict({"schema_version": SCHEMA_VERSION, "repos": [{
            "repo_id": "x", "path": "/x", "git": {"head": "short"},
        }]})
    with pytest.raises(ValueError, match="unknown bundled profile_id"):
        TargetSpec.from_dict({"schema_version": SCHEMA_VERSION, "repos": [{
            "repo_id": "x", "path": "/x", "facets": [{
                "profile_id": "language.unknown", "kind": "language",
                "scope_roots": ["."], "evidence": ["x.ext"],
            }],
        }]})
    with pytest.raises(ValueError, match="kind does not match"):
        TargetSpec.from_dict({"schema_version": SCHEMA_VERSION, "repos": [{
            "repo_id": "x", "path": "/x", "facets": [{
                "profile_id": "language.go", "kind": "framework",
                "scope_roots": ["."], "evidence": ["go.mod"],
            }],
        }]})
    with pytest.raises(ValueError, match="duplicate profile_id"):
        TargetSpec.from_dict({"schema_version": SCHEMA_VERSION, "repos": [{
            "repo_id": "x", "path": "/x", "facets": [{
                "profile_id": "language.go", "kind": "language",
                "scope_roots": ["."], "evidence": ["go.mod"],
            }, {
                "profile_id": "language.go", "kind": "language",
                "scope_roots": ["cmd"], "evidence": ["cmd/main.go"],
            }],
        }]})


def test_root_paths_default_to_repo_root():
    r = RepoTarget(repo_id="x-1", path="/tmp/x")
    base = Path("/tmp/x").resolve()
    assert r.root_paths() == [base]
    r2 = RepoTarget(repo_id="x-1", path="/tmp/x", analysis_roots=["src", "lib"])
    assert r2.root_paths() == [base / "src", base / "lib"]

"""Executor behavior classes (57B-10 acceptance #3, #5, #6) — all against
generic synthetic repos; fake tools are bash one-liners."""

import json

from doctor_wrapper.executor import run_tool
from doctor_wrapper.status import Status
from doctor_wrapper.tooldefs import ToolDef

SCAN_DATE = "2026-07-16"


def bash_tool(name, script, **kw) -> ToolDef:
    return ToolDef(
        name=name,
        binary="bash",
        argv_builder=lambda t: ["bash", "-c", script],
        version_argv=["bash", "--version"],
        **kw,
    )


def run(td, target, tmp_path, *, allow_network=False):
    return run_tool(
        td, target, tmp_path / "signals", SCAN_DATE,
        allow_network=allow_network,
    )


def test_findings_exit_is_complete(target, tmp_path):
    td = bash_tool("finder", "echo finding; exit 1", normal_exits=frozenset({0, 1}))
    r = run(td, target, tmp_path)
    assert r.status is Status.COMPLETE and r.manifest.exit_code == 1


def test_error_exit_is_failed(target, tmp_path):
    r = run(bash_tool("crasher", "exit 2"), target, tmp_path)
    assert r.status is Status.FAILED and "error exit 2" in r.reason


def test_killed_tool_is_failed_never_silent(target, tmp_path):
    r = run(bash_tool("victim", "kill -9 $$"), target, tmp_path)
    assert r.status is Status.FAILED
    assert r.manifest.status == "failed"  # manifest written despite the kill


def test_timeout_is_failed(target, tmp_path):
    r = run(bash_tool("sleeper", "sleep 5", timeout_s=1), target, tmp_path)
    assert r.status is Status.FAILED and "timeout" in r.reason


def test_network_error_on_attempted_run_is_failed(target, tmp_path):
    r = run(bash_tool("nettool", "echo 'getaddrinfo ENOTFOUND registry' >&2; exit 0",
                      network=True),
            target, tmp_path, allow_network=True)
    assert r.status is Status.FAILED
    assert r.reason == "network/auth error during attempted run"


def test_npm_style_http_error_is_not_misreported_as_complete(target, tmp_path):
    r = run(
        bash_tool("net-http", "echo '{}' ; echo 'npm error E404' >&2; exit 1",
                  network=True, normal_exits=frozenset({0, 1})),
        target, tmp_path, allow_network=True,
    )
    assert r.status is Status.FAILED
    assert r.reason == "network/auth error during attempted run"


def test_network_words_in_local_tool_output_are_not_errors(target, tmp_path):
    r = run(bash_tool("local", "echo 'network helper; offline cache documentation'"),
            target, tmp_path)
    assert r.status is Status.COMPLETE


def test_offline_preflight_is_skipped_without_invocation(target, tmp_path):
    marker = tmp_path / "network-invoked"
    td = bash_tool("offline", f"touch '{marker}'", network=True,
                   preflight=lambda: "offline/DNS")
    r = run(td, target, tmp_path, allow_network=True)
    assert r.status is Status.SKIPPED and "preflight unavailable" in r.reason
    assert not marker.exists()


def test_malformed_output_is_failed(target, tmp_path):
    td = bash_tool(
        "jsontool", "echo 'not json'",
        output_validator=lambda out, ec: "" if out.strip().startswith("{") else "expected JSON object",
    )
    r = run(td, target, tmp_path)
    assert r.status is Status.FAILED and "malformed output" in r.reason


def test_missing_tool_is_skipped_with_manifest(target, tmp_path):
    td = ToolDef(name="ghost", binary="no-such-binary-xyz",
                 argv_builder=lambda t: ["no-such-binary-xyz"])
    r = run(td, target, tmp_path)
    assert r.status is Status.SKIPPED and "not installed" in r.reason
    assert r.manifest.exit_code is None  # never invoked


def test_guard_refusal_is_skipped_before_invocation(target, tmp_path):
    marker = tmp_path / "invoked"
    td = bash_tool("guarded", f"touch {marker}",
                   guards=[lambda t: "policy says no"])
    r = run(td, target, tmp_path)
    assert r.status is Status.SKIPPED and "guard refusal" in r.reason
    assert not marker.exists(), "guarded tool must never be invoked"


def test_degrader_demotes_to_partial(target, tmp_path):
    td = bash_tool("partialtool", "echo ok",
                   degraders=[lambda t, out, ec: ">15% unresolved edges"])
    r = run(td, target, tmp_path)
    assert r.status is Status.PARTIAL and "unresolved" in r.reason


def test_target_mutation_is_failed_loudly(target, tmp_path):
    td = bash_tool("mutator", "echo oops > injected.txt")
    r = run(td, target, tmp_path)
    assert r.status is Status.FAILED and "TARGET MUTATED" in r.reason
    # cleanup so other assertions on the fixture aren't affected
    (tmp_path / "widget-api" / "injected.txt").unlink(missing_ok=True)


def test_already_dirty_target_is_valid_when_unchanged(target, tmp_path, synthetic_repo):
    (synthetic_repo / "wip.txt").write_text("uncommitted\n")  # pre-dirty
    # Discovery records the dirty state before execution.
    from doctor_wrapper import gitinfo
    target.git.dirty_detail = gitinfo.dirty_detail(synthetic_repo)
    r = run(bash_tool("reader", "cat index.js > /dev/null"), target, tmp_path)
    assert r.status is Status.COMPLETE, "identical pre/post dirty state must pass"


def test_manifest_is_structured_and_sanitized(target, tmp_path):
    td = bash_tool("leaker", "echo 'token=abc123'",
                   env={"MY_FLAG": "1"})
    r = run(td, target, tmp_path)
    jpath = tmp_path / "signals" / f"leaker-{target.repo_id}.manifest.json"
    data = json.loads(jpath.read_text())
    assert data["argv"][0] == "bash" and isinstance(data["argv"], list)
    assert data["env"] == {"MY_FLAG": "1"}
    assert data["repos"][0]["repo_head"] == target.git.head
    # raw output retains the secret (containment zone)...
    assert "abc123" in r.raw_path.read_text()
    # ...but nothing persisted through sanitize leaks it
    assert "abc123" not in jpath.read_text()

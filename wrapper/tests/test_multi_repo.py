from analysis_wrapper import gitinfo
from analysis_wrapper.executor import run_tool
from analysis_wrapper.status import Status
from analysis_wrapper.targetspec import (
    GitProvenance, RepoTarget, TechnologyFacet, stable_repo_id,
)

from test_executor import bash_tool, identities_for


def _target(repo):
    return RepoTarget(
        repo_id=stable_repo_id(str(repo)), path=str(repo), facets=[
            TechnologyFacet("language.javascript", "language", ["."], ["index.js"])
        ],
        git=GitProvenance(head=gitinfo.head(repo), branch="main",
                          dirty_detail=gitinfo.dirty_detail(repo), commit_count=1),
    )


def test_multi_repo_manifest_stamps_every_repo(synthetic_repo, tmp_path):
    import subprocess
    second = tmp_path / "second"
    second.mkdir(); (second / "x.js").write_text("x\n")
    subprocess.run(["git", "-C", str(second), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(second), "-c", "user.name=t", "-c", "user.email=t@t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(second), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "init"], check=True)
    (second / "work in progress.js").write_text("dirty\n")
    first, other = _target(synthetic_repo), _target(second)
    identities = identities_for(first, other)
    result = run_tool(bash_tool("multi", "echo ok"), first, tmp_path / "signals",
                      "2026-07-16", identities.repository(first.repo_id),
                      additional_targets=[other],
                      additional_repository_identities=[
                          identities.repository(other.repo_id)], signal_id="multi-two")
    assert result.status is Status.COMPLETE
    assert [x.repository_ref for x in result.manifest.repos] == [
        "widget-api", "second"]
    detail = result.manifest.repos[1].dirty_detail
    assert detail.startswith("yes (1 files: ??") and "work in progress.js" in detail


def test_multi_repo_mutation_of_secondary_fails(synthetic_repo, tmp_path):
    import subprocess
    second = tmp_path / "second-mutate"
    second.mkdir(); (second / "x.js").write_text("x\n")
    subprocess.run(["git", "-C", str(second), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(second), "-c", "user.name=t", "-c", "user.email=t@t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(second), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "init"], check=True)
    first, other = _target(synthetic_repo), _target(second)
    identities = identities_for(first, other)
    result = run_tool(
        bash_tool("multi-mutator", f"touch '{second / 'bad.js'}'"), first,
        tmp_path / "signals", "2026-07-16", identities.repository(first.repo_id),
        additional_targets=[other], additional_repository_identities=[
            identities.repository(other.repo_id)],
    )
    assert result.status is Status.FAILED and "TARGET MUTATED" in result.reason

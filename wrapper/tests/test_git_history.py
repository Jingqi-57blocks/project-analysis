import subprocess

from doctor_wrapper.git_history.identity import IdentityResolver, is_bot
from doctor_wrapper.git_history.worker import analyze


def _git(repo, *args, name="A", email="a@example.invalid"):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", f"user.name={name}", "-c", f"user.email={email}", *args],
        check=True, capture_output=True, text=True,
    )


def _commit(repo, message, name="A", email="a@example.invalid"):
    _git(repo, "add", "-A", name=name, email=email)
    _git(repo, "commit", "-qm", message, name=name, email=email)


def test_bot_is_excluded_before_identity_resolution():
    assert is_bot("dependabot[bot]", "bot@example.invalid")
    resolver = IdentityResolver(".", [("Human", "h@example.invalid")])
    assert resolver.resolve("Human", "h@example.invalid") == "Human"


def test_same_name_different_email_is_uncertain_not_merged(tmp_path):
    repo = tmp_path / "r"; repo.mkdir(); _git(repo, "init", "-q", "-b", "main")
    resolver = IdentityResolver(str(repo), [("Alex", "one@example.invalid"),
                                           ("Alex", "two@example.invalid")])
    assert resolver.resolve("Alex", "one@example.invalid") == "Alex"
    assert resolver.uncertain_name_matches == ["alex"]


def test_exact_email_and_mailmap_are_strong_identity_evidence(tmp_path):
    repo = tmp_path / "identity"; repo.mkdir(); _git(repo, "init", "-q", "-b", "main")
    (repo / ".mailmap").write_text(
        "Canonical <canonical@example.invalid> Alias <alias@example.invalid>\n"
    )
    resolver = IdentityResolver(str(repo), [
        ("First", "shared@example.invalid"),
        ("Second", "shared@example.invalid"),
        ("Alias", "alias@example.invalid"),
        ("Canonical", "canonical@example.invalid"),
    ])
    assert resolver.resolve("First", "shared@example.invalid") == \
        resolver.resolve("Second", "shared@example.invalid")
    assert resolver.resolve("Alias", "alias@example.invalid") == \
        resolver.resolve("Canonical", "canonical@example.invalid")


def test_history_emits_rename_ownership_and_bulk_guard(tmp_path):
    repo = tmp_path / "history"; repo.mkdir(); _git(repo, "init", "-q", "-b", "main")
    (repo / "old.js").write_text("one\n"); _commit(repo, "first")
    _git(repo, "mv", "old.js", "new.js"); _commit(repo, "rename", name="B", email="b@example.invalid")
    (repo / "new.js").write_text("one\ntwo\n"); _commit(repo, "change")
    for i in range(4):
        (repo / f"bulk{i}.js").write_text(f"{i}\n")
    _commit(repo, "bulk")
    result = analyze(str(repo), "2000-01-01", top=20, min_shared=1, bulk_limit=2)
    assert any(x["old"] == "old.js" and x["final"] == "new.js" for x in result["rename_aliases"])
    assert any(x["file"] == "new.js" and "dominant_commit_share" in x for x in result["ownership"])
    assert result["bulk_changesets_excluded_from_coupling"] == 1
    assert result["history_completeness"]["total_commits_head"] == 4


def test_cross_dir_coupling_survives_top_n_truncation(tmp_path):
    repo = tmp_path / "cross"; repo.mkdir(); _git(repo, "init", "-q", "-b", "main")
    (repo / "api").mkdir(); (repo / "ui").mkdir()
    # 3 commits touching an api file AND a ui file together -> cross-dir pair.
    for i in range(3):
        (repo / "api" / "svc.go").write_text(f"a{i}\n")
        (repo / "ui" / "page.tsx").write_text(f"u{i}\n")
        _commit(repo, f"cross {i}")
    result = analyze(str(repo), "2000-01-01", top=20, min_shared=2, bulk_limit=50)
    pairs = result["cross_dir_coupling"]
    assert any({p["file_a"], p["file_b"]} == {"api/svc.go", "ui/page.tsx"} for p in pairs)
    # a same-dir-only pair must NOT appear in the cross-dir list
    assert all(p["file_a"].split("/")[0] != p["file_b"].split("/")[0] for p in pairs)

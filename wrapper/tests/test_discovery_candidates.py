"""57B-11 S6: integration candidates — every signal kind exercised, no name lists."""

from analysis_wrapper.discovery.candidates import generate


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_dependency_only_labeling(tmp_path):
    report = generate(tmp_path, "r-1", dependencies={"leftpad": "1.0.0"})
    (candidate,) = report.candidates
    assert candidate.value == "leftpad"
    assert candidate.signal_kind == "dependency-only"
    assert candidate.evidence == ["package.json (dependency)"]


def test_import_and_client_init_merge_with_dependency(tmp_path):
    _write(tmp_path / "mail.js",
           "const sdk = require('mail-sdk');\n"
           "const client = sdk.createClient({});\n")
    report = generate(tmp_path, "r-1", dependencies={"mail-sdk": "2.0.0"})
    (candidate,) = report.candidates
    assert candidate.value == "mail-sdk"
    assert candidate.signal_kind == "client_init+dependency+import"
    assert any("mail.js:1 (import)" in e for e in candidate.evidence)
    assert any("mail.js:2 (client_init)" in e for e in candidate.evidence)


def test_scoped_js_package_and_go_module_keys(tmp_path):
    _write(tmp_path / "a.ts", "import { S } from '@corp/storage/sub';\n")
    _write(tmp_path / "b.go",
           'package b\nimport "cloud.example.dev/sdk/storage/v2"\n')
    report = generate(tmp_path, "r-1")
    values = {c.value for c in report.candidates}
    assert "@corp/storage" in values
    assert "cloud.example.dev/sdk/storage" in values


def test_outbound_endpoint_vs_config_and_oauth(tmp_path):
    _write(tmp_path / "call.js",
           "fetch('https://api.payments.example.io/v1/charge');\n")
    _write(tmp_path / "settings.yaml",
           "auth_url: https://login.identity.example.net/oauth/authorize\n")
    report = generate(tmp_path, "r-1")
    by_value = {c.value: c for c in report.candidates}
    assert by_value["api.payments.example.io"].signal_kind == "outbound_endpoint"
    oauth = by_value["login.identity.example.net"]
    assert set(oauth.signal_kind.split("+")) == {"config", "oauth_provider"}


def test_env_names_only_never_values(tmp_path):
    _write(tmp_path / ".env.example", "PAYMENT_API_KEY=super-secret-value\n")
    _write(tmp_path / "cfg.go", 'package c\nfunc f() string { return os.Getenv("QUEUE_URL") }\n')
    _write(tmp_path / "cfg.js", "const x = process.env.WEBHOOK_TARGET;\n")
    _write(tmp_path / "vite.ts", "const api = import.meta.env.VITE_MAIN_API;\n")
    report = generate(tmp_path, "r-1")
    values = {c.value for c in report.candidates}
    assert {"PAYMENT_API_KEY", "QUEUE_URL", "WEBHOOK_TARGET", "VITE_MAIN_API"} <= values
    dumped = " ".join(e for c in report.candidates for e in c.evidence) + \
        " ".join(values)
    assert "super-secret-value" not in dumped


def test_tracked_env_file_yields_names_and_endpoint_hosts(tmp_path):
    import subprocess
    _write(tmp_path / ".env.production",
           "MAIN_API=https://api.internal.example.co/base\n"
           "API_TOKEN=tok_secretvalue123\n")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t",
                    "-c", "user.name=t", "add", "-A"], check=True)
    report = generate(tmp_path, "r-1")
    by_value = {c.value: c for c in report.candidates}
    assert "MAIN_API" in by_value and "API_TOKEN" in by_value      # names
    assert by_value["api.internal.example.co"].signal_kind == "config"  # host from value
    dumped = " ".join(c.value for c in report.candidates) + \
        " ".join(e for c in report.candidates for e in c.evidence)
    assert "tok_secretvalue123" not in dumped                      # values never


def test_untracked_env_file_yields_names_but_no_hosts(tmp_path):
    _write(tmp_path / ".env", "LOCAL_API=https://staging.hidden.example.co/x\n")
    report = generate(tmp_path, "r-1")  # no git repo -> nothing tracked
    values = {c.value for c in report.candidates}
    assert "LOCAL_API" in values
    assert "staging.hidden.example.co" not in values


def test_ci_resources_from_pipelines(tmp_path):
    _write(tmp_path / "bitbucket-pipelines.yml",
           "image: node:18\npipelines:\n  default:\n    - step:\n"
           "        script:\n          - pipe: corp/deploy-pipe:1.2\n")
    _write(tmp_path / ".github" / "workflows" / "ci.yml",
           "jobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n")
    report = generate(tmp_path, "r-1")
    ci = {c.value for c in report.candidates if "ci_resource" in c.signal_kind}
    assert {"node:18", "corp/deploy-pipe:1.2", "actions/checkout@v4"} <= ci


def test_noise_hosts_filtered_and_disclosed(tmp_path):
    _write(tmp_path / "dev.js", "fetch('http://localhost:3000/x');\n")
    report = generate(tmp_path, "r-1")
    assert report.candidates == []
    assert any("noise filter" in n for n in report.notes)


def test_tier2_and_vendored_trees_not_scanned(tmp_path):
    _write(tmp_path / "node_modules" / "x" / "index.js",
           "fetch('https://tracker.vendored.example.org/x');\n")
    _write(tmp_path / "docs" / "gen.js",
           "fetch('https://generated.example.org/x');\n")
    report = generate(tmp_path, "r-1", tier2_exclusions=["docs"])
    assert report.candidates == []


def test_deterministic_ordering_and_stable_ids(tmp_path):
    _write(tmp_path / "a.js", "require('zeta'); require('alpha');\n")
    first = generate(tmp_path, "r-1")
    second = generate(tmp_path, "r-1")
    assert [c.value for c in first.candidates] == ["alpha", "zeta"]
    assert [c.candidate_id for c in first.candidates] == \
        [c.candidate_id for c in second.candidates]
    assert first.candidates[0].candidate_id == "r-1:alpha"

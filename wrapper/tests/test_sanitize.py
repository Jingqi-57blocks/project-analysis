"""Redaction fixtures — the spike's confirmed-bug cases are the floor."""

import json

from doctor_wrapper.sanitize import REDACTED, bound, redact, sanitize_text

MUST_REDACT = [
    ('{"token": "abc123"}', "abc123"),                    # quoted JSON value
    ("'apiKey' : 'zzz999'", "zzz999"),                    # single quotes
    ("secret=hunter1", "hunter1"),                        # key=value
    ("password: pass1", "pass1"),                         # key: value
    ("Authorization: Bearer eyJhbGciOi.x", "eyJhbGciOi.x"),  # bearer token
    ("Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),               # basic scheme
    ("export DB_PASSWORD='hunter2'", "hunter2"),          # compound env key (P0 regression)
    ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI", "wJalrXUtnFEMI"),
    ("MYSQL_ROOT_PASSWORD: rootpw", "rootpw"),
    ('authToken="xyz789"', "xyz789"),                     # camelCase compound
    ("https://user:ghp_tok3n@github.com/x.git", "ghp_tok3n"),  # URL credential
]

MUST_NOT_TOUCH = [
    '"jsonwebtoken": "^9.0.0"',       # package name containing 'token'
    "src/password-reset/index.js",    # keyword in a path, no separator
    "69 lines / 302 tokens",          # counter word
    "| Tokens | 302 |",               # table header
]


def test_secret_values_redacted():
    for text, secret in MUST_REDACT:
        out = redact(text)
        assert secret not in out, f"leak: {text!r} -> {out!r}"
        assert REDACTED in out, f"no redaction marker: {text!r} -> {out!r}"


def test_escaped_quote_in_json_secret_is_fully_redacted_and_json_stays_valid():
    source = r'{"token":"abc\"def","safe":"visible"}'
    output = sanitize_text(source)
    assert "abc" not in output and "def" not in output
    assert json.loads(output) == {"token": REDACTED, "safe": "visible"}


def test_false_positives_untouched():
    for text in MUST_NOT_TOUCH:
        assert redact(text) == text, f"over-redacted: {text!r}"


def test_ansi_stripped_and_paths_relativized(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/someone")
    out = sanitize_text("\x1b[1mClone\x1b[22m at /Users/someone/proj/a.ts")
    assert "\x1b[" not in out
    assert out == "Clone at $HOME/proj/a.ts"


def test_rootless_absolute_paths_from_table_renderers_are_relativized(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/someone")
    out = sanitize_text(
        "| finding | Users/someone/project/package-lock.json |\n"
        "ordinary users/someone and someone else"
    )
    assert "$HOME/project/package-lock.json" in out
    assert "Users/someone/project" not in out
    assert "ordinary users/someone and someone else" in out


def test_single_component_home_does_not_rewrite_prose(monkeypatch):
    monkeypatch.setenv("HOME", "/root")
    assert sanitize_text("root cause at /root/project") == "root cause at $HOME/project"
    assert sanitize_text("/rooted/project") == "/rooted/project"


def test_bound_header_counts_and_cap():
    text = "\n".join(f"line{i}" for i in range(10))
    out = bound(text, 3)
    assert out.startswith("sample: 3 of 10 lines (bound=3)\n")
    assert "line2" in out and "line3" not in out
    # under the cap: retained == total
    assert bound("a\nb", 5).startswith("sample: 2 of 2 lines (bound=5)\n")

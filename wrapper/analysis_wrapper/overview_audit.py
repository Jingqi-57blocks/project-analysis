"""Generic cross-stage consistency audit for overview artifacts.

The audit compares structured producer declarations with structured consumer
counts.  It intentionally does not inspect business words in narrative output;
semantic interpretation remains bounded by evidence-basis rules in synthesis.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from . import (coverage_render, findings, identity, locale, module_map, module_render,
               run_provenance, synthesis_input, workspace_metrics)
from .executor import replace_artifact_text
from .sanitize import sanitize_text
from .targetspec import TargetSpec, overlapping_repo_pairs

SCHEMA_VERSION = "2.0.0"
_FENCE = re.compile(r"```.*?```", re.S)
_MERMAID_FENCE = re.compile(r"```mermaid\s*\n(.*?)```", re.S | re.IGNORECASE)
_HTML_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);")
_ENCODED_LOCAL_LINK = re.compile(
    r"\]\((?!https?://)[^\s)]*%[0-9A-Fa-f]{2}[^)]*\)", re.IGNORECASE)
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WORD = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9'-]*\b")
_MERMAID_VALIDATOR = Path(__file__).parent / "report_html" / "validate_mermaid.js"

# -- 57B-111 delivered-language completeness gate --------------------------- #
#
# The SKILL.md "Standing scope disclaimer" section is canonical English, with
# an instruction that a non-English run include "a faithful translation making
# exactly the same scope claims". The disclaimer is therefore LLM-authored
# prose per run, not a template string this module can byte-match \u2014 so each
# delivered language instead registers one short, distinctive marker phrase
# any faithful translation is expected to carry (the same role
# `test_skill_hygiene.py`'s ``DISCLAIMER_MARK`` plays for the English
# templates). Add an entry here when a new language is delivered. Only
# non-English languages are looked up here (the check below only runs when
# `language != "en"`), so no "en" entry is needed.
_DISCLAIMER_MARKERS: dict[str, str] = {
    "zh-CN": "\u4ee3\u7801\u4ed3\u5e93\u8bc1\u636e",
}

# Verbatim-cited source tokens (UI labels quoted from the product, code
# identifiers, error strings, endpoints, citations, file paths, URLs) stay in
# their source language in ANY run language and must never be flagged as
# leakage -- these patterns strip them before the prose heuristic runs.
_FENCED_OR_INLINE_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
_QUOTED_SPAN = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d|\'[^\'\n]*\'')
_URL = re.compile(r"https?://\S+")
_CITATION_OR_PATH = re.compile(
    r"\b[\w.\-]+@(?:[0-9a-fA-F]{7,40}|WORKTREE|NON-GIT):\S+"  # repo@commit:path:line
    r"|(?<![\w./\\-])(?:[\w.\-]+[/\\])+[\w.\-]+"               # a/b.c style paths
)
_LATIN_WORD_RUN = re.compile(r"(?:[A-Za-z][A-Za-z'\-]*\s+){5,}[A-Za-z][A-Za-z'\-]*")
_CAMEL_OR_SNAKE_OR_DOTTED = re.compile(r"^[a-z0-9]+[A-Z]|_|\.[A-Za-z]")
_ENGLISH_STOPWORDS = {
    "the", "and", "of", "to", "in", "is", "are", "this", "that", "for",
    "with", "on", "as", "by", "from", "an", "be", "has", "have", "it", "its",
    "was", "were", "which", "not", "or", "if", "can", "will", "would",
    "should", "must", "at", "into", "than", "when", "where", "what", "who",
    "does", "do", "any", "all", "but", "so", "there", "these", "those",
}


def _strip_verbatim_tokens(text: str) -> str:
    """Remove code/mermaid, inline code, quoted spans, URLs, citations and
    file paths before the stray-English-prose heuristic runs, so verbatim
    tokens quoted in ANY run language are never mistaken for leaked prose."""
    text = _FENCED_OR_INLINE_CODE.sub(" ", text)
    text = _QUOTED_SPAN.sub(" ", text)
    text = _URL.sub(" ", text)
    text = _CITATION_OR_PATH.sub(" ", text)
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("|"))


def _stray_english_prose(text: str) -> list[str]:
    """Low-false-positive heuristic: sentence-like runs of Latin-script words
    that contain several distinct common English function words, after
    stripping every verbatim-token category (see ``_strip_verbatim_tokens``).
    A lone code identifier or citation never trips this on its own -- it takes
    a genuine run of ordinary English sentence structure to match.
    """
    cleaned = _strip_verbatim_tokens(text)
    hits: list[str] = []
    for match in _LATIN_WORD_RUN.finditer(cleaned):
        run = match.group(0)
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", run)
                 if not _CAMEL_OR_SNAKE_OR_DOTTED.search(w)]
        stop_hits = {w.lower() for w in words if w.lower() in _ENGLISH_STOPWORDS}
        if len(stop_hits) >= 3:
            hits.append(run.strip())
    return hits


def _load(path: Path) -> dict:
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _pm_reading_minutes(markdown: str) -> float:
    prose = _FENCE.sub("", markdown)
    prose = "\n".join(line for line in prose.splitlines()
                      if not line.lstrip().startswith("|"))
    cjk = len(_CJK.findall(prose))
    latin = len(_WORD.findall(_CJK.sub(" ", prose)))
    return cjk / 500.0 + latin / 250.0


def _is_source_path_label(label: str) -> bool:
    """Return whether a graph ``file`` label is specific enough to gate prose.

    Dependency producers are package-granular in some languages and therefore
    also materialize labels such as ``.``, ``init`` and ``config`` as file-node
    endpoints. Those are ordinary prose words, not safely matchable paths.
    A slash, filename suffix, or conventional extensionless build filename is
    required before an exact-label match can be treated as a source-path leak.
    """
    value = label.strip()
    if not value or value in {".", "..", "(unknown)", "unknown"}:
        return False
    if "/" in value or "\\" in value:
        return True
    basename = Path(value).name
    if basename in {"Dockerfile", "Makefile", "Procfile", "Rakefile"}:
        return True
    return bool(Path(basename).suffix) and not basename.startswith(".")


def _has_source_citation(text: str, identities: identity.IdentityMap) -> bool:
    return any(re.search(
        rf"(?<![A-Za-z0-9._/%-]){re.escape(item.reference)}@"
        r"(?:[0-9a-fA-F]{7,40}|WORKTREE|NON-GIT):",
        text,
    ) for item in identities.repositories)


def _mermaid_integrity_problems(name: str, markdown: str) -> list[str]:
    problems: list[str] = []
    blocks = _MERMAID_FENCE.findall(markdown)
    for index, body in enumerate(blocks, 1):
        if "-;" in body or ";->" in body:
            problems.append(f"{name} mermaid block {index}: invalid edge token")
        if _HTML_ENTITY.search(body) or re.search(r"%[0-9A-Fa-f]{2}", body):
            problems.append(f"{name} mermaid block {index}: encoded punctuation")
    if not blocks:
        return problems

    node = shutil.which("node")
    if not node:
        return problems + [f"{name}: Mermaid parser unavailable (node not found)"]
    try:
        proc = subprocess.run(
            [node, str(_MERMAID_VALIDATOR)],
            input=json.dumps(blocks), text=True, capture_output=True,
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return problems + [f"{name}: Mermaid parser failed: {exc}"]
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown parser error").strip()
        return problems + [f"{name}: Mermaid parser failed: {detail[:300]}"]
    try:
        results = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return problems + [f"{name}: Mermaid parser returned malformed output"]
    for index, result in enumerate(results, 1):
        if not result.get("valid", False):
            detail = str(result.get("error", "parse failed")).replace("\n", " ")
            problems.append(f"{name} mermaid block {index}: {detail[:300]}")
    return problems


def audit(run_dir: str | Path, *, require_module_map: bool = False,
          require_reports: bool = False) -> dict:
    run = Path(run_dir).expanduser().resolve()
    checks: list[dict] = []

    def check(code: str, passed: bool, detail: str) -> None:
        checks.append({"check": code, "status": "pass" if passed else "fail",
                       "detail": detail})

    def warn(code: str, triggered: bool, detail: str) -> None:
        checks.append({"check": code, "status": "warn" if triggered else "pass",
                       "detail": detail})

    spec = TargetSpec.load(run / "targets.json")
    identities = identity.load(run)

    # Run language, for the delivered-language completeness gate (57B-111).
    # Older runs may predate run-provenance.json entirely; default to "en" so
    # this gate is inert for them rather than raising -- absence is a
    # defensible compatibility choice for pre-gate runs. A *present but
    # unreadable* file is different: it means a run this analyzer claims to
    # understand cannot actually be verified, so this fails closed instead of
    # silently reverting to "en" (which would skip every language check for a
    # run that may not even be English).
    language = "en"
    provenance_path = run_provenance.path_for(run)
    if provenance_path.is_file():
        try:
            language = run_provenance.load(run).get("generation", {}).get(
                "language") or "en"
        except ValueError as exc:
            check(
                "run-provenance-readable", False,
                "run-provenance.json is present but unreadable; cannot "
                f"verify run language: {exc}",
            )

    if language != "en":
        # (a) Fallback leakage: the precise, non-heuristic signal -- a
        # catalog that falls back to English for any key, or "translates" a
        # key to a byte-identical copy of English, is by definition not
        # delivering the run's primary language natively. This is
        # deliberately checked at the catalog level (not by scanning rendered
        # text for English) because the catalog signal is exact and reliable
        # where text heuristics are not. Both missing keys (silent fallback)
        # and non-allowlisted mirrored keys (uncaught English copy) are hard
        # failures here -- key *presence* alone was never a sufficient proxy
        # for "this renders in the run's language".
        missing_catalog_keys = locale.missing_keys(language)
        mirrored_catalog_keys = locale.mirrored_keys(language)
        catalog_problems = bool(missing_catalog_keys) or bool(mirrored_catalog_keys)
        detail_parts = []
        if missing_catalog_keys:
            detail_parts.append(
                f"falls back to English for {len(missing_catalog_keys)} "
                f"key(s): " + ", ".join(missing_catalog_keys[:20]))
        if mirrored_catalog_keys:
            detail_parts.append(
                f"has {len(mirrored_catalog_keys)} non-allowlisted key(s) "
                f"byte-identical to English (not actually translated): "
                + ", ".join(mirrored_catalog_keys[:20]))
        check(
            "language-catalog-completeness", not catalog_problems,
            f"{language!r} label catalog is key-complete and has no "
            f"non-allowlisted English-mirrored values"
            if not catalog_problems else
            f"{language!r} label catalog " + "; ".join(detail_parts),
        )

    overlaps = overlapping_repo_pairs(spec.repos)
    check(
        "non-overlapping-targets", not overlaps,
        "canonical targets are pairwise non-overlapping" if not overlaps else
        "overlapping target pairs: "
        + ", ".join(f"{left}/{right}" for left, right in overlaps),
    )
    capabilities = _load(run / "capabilities.json")
    model = _load(run / "system-model.json")
    coverage = model.get("coverage", {})

    current_contracts = [
        "discovery-report.json", "capabilities.json", "callgraph-coverage.json",
        "imports/depmap-coverage.json", "system-model.json",
        "module-candidates.json", "workspace-metrics.json",
        "synthesis-input.json", "signals/run-summary.json",
    ]
    if (run / "module-map.json").is_file():
        current_contracts.append("module-map.json")
    if (run / "findings.json").is_file():
        current_contracts.append("findings.json")
    version_problems = []
    for rel in current_contracts:
        path = run / rel
        if not path.is_file():
            continue
        version = _load(path).get("schema_version")
        if version != SCHEMA_VERSION:
            version_problems.append(f"{rel}: {version!r}")
    for path in sorted((run / "signals").glob("*.manifest.json")):
        version = _load(path).get("schema_version")
        if version != SCHEMA_VERSION:
            version_problems.append(f"{path.relative_to(run)}: {version!r}")
    check("artifact-contract-versions", not version_problems,
          "all machine evidence uses the current 2.0.0 contract"
          if not version_problems else
          "unsupported artifact contracts: " + "; ".join(version_problems[:20]))

    metrics_path = run / workspace_metrics.FILENAME
    if metrics_path.is_file():
        observed_metrics = _load(metrics_path)
        expected_metrics = workspace_metrics.build(run)
        check("workspace-metrics-recomputation", observed_metrics == expected_metrics,
              "workspace metrics match authoritative manifests, dependency maps, and graph"
              if observed_metrics == expected_metrics else
              "workspace-metrics.json differs from authoritative producer artifacts")
        synthesis_path = run / "synthesis-input.json"
        if synthesis_path.is_file():
            packet_metrics = _load(synthesis_path).get("workspace_metrics", {})
            expected_projection = synthesis_input._workspace_metrics_projection(
                observed_metrics)
            check("workspace-metrics-consumption", packet_metrics == expected_projection,
                  "synthesis input contains the exact bounded workspace metrics projection"
                  if packet_metrics == expected_projection else
                  "synthesis input omits or changes the workspace metrics projection")
    else:
        check("workspace-metrics-recomputation", False,
              "workspace-metrics.json is missing")

    # Signal summary is a consumer-facing index.  It may not claim a different
    # status than the per-signal manifest that actually recorded execution.
    signal_summary_path = run / "signals" / "run-summary.json"
    if signal_summary_path.is_file():
        signal_summary = _load(signal_summary_path)
        signal_mismatches = []
        for row in signal_summary.get("signals", []):
            view_name = str(row.get("view", ""))
            if row.get("status") in {"complete", "partial"} and (
                    not view_name or not (run / "signals" / view_name).is_file()):
                signal_mismatches.append(
                    f"{row.get('repository_ref', '')}/{row.get('tool', '')}: valid view missing")
            manifest_name = str(row.get("manifest", ""))
            manifests = [run / "signals" / manifest_name] if manifest_name else []
            if len(manifests) != 1 or not manifests[0].is_file():
                signal_mismatches.append(
                    f"{row.get('repository_ref', '')}/{row.get('tool', '')}: manifest missing")
                continue
            manifest = _load(manifests[0])
            if manifest.get("status") != row.get("status"):
                signal_mismatches.append(
                    f"{manifests[0].name}: {manifest.get('status')} != {row.get('status')}")
        check("signal-summary-consistency", not signal_mismatches,
              "summary statuses match per-signal manifests" if not signal_mismatches
              else "; ".join(signal_mismatches[:20]))

    for capability in capabilities.get("capabilities", []):
        missing = capability.get("missing_artifacts", [])
        expected = capability.get("expected_artifacts", [])
        applicable = capability.get("status") != "not-applicable"
        check(
            f"artifact:{capability.get('capability_id', '')}",
            not applicable or not missing,
            ("all canonical artifacts present" if not missing else
             f"missing canonical artifacts: {', '.join(missing)}")
            + ("" if expected else "; capability has no file artifact"),
        )

    capability_by_id = {row.get("capability_id", ""): row
                        for row in capabilities.get("capabilities", [])}
    partition_mapping = {
        "callgraph": "symbols_and_calls",
        "dependency-map": "dependency_imports",
        "route-inventory": "routes",
        "data-model": "tables",
    }
    contradictions = []
    for capability_id, partition_id in partition_mapping.items():
        cap_state = capability_by_id.get(capability_id, {}).get("status", "failed")
        part_state = coverage.get(partition_id, {}).get("status", "failed")
        valid = True
        if capability_id == "data-model":
            valid = cap_state == part_state
        elif cap_state == "not-applicable":
            valid = part_state == "not-applicable"
        elif cap_state == "failed":
            valid = part_state == "failed"
        elif cap_state == "unavailable":
            valid = part_state in {"unavailable", "partial"}
        elif cap_state in {"complete", "partial"}:
            valid = part_state not in {"failed", "unavailable", "not-applicable"}
        if not valid:
            contradictions.append(
                f"{capability_id}={cap_state} but {partition_id}={part_state}")
    check("capability-partition-consistency", not contradictions,
          "capability and system-model coverage states agree" if not contradictions
          else "; ".join(contradictions))

    misplaced = []
    for rel in ("signals/callgraph-coverage.json", "signals/system-model.json",
                "signals/imports/depmap-coverage.json"):
        if (run / rel).exists():
            misplaced.append(rel)
    check("canonical-placement", not misplaced,
          "no deterministic producer artifacts are nested under signals/"
          if not misplaced else f"misplaced artifacts: {', '.join(misplaced)}")

    call_cov_path = run / "callgraph-coverage.json"
    if call_cov_path.is_file():
        call_cov = _load(call_cov_path)
        declared = sum(int(row.get("edges_emitted", 0))
                       for row in call_cov.get("repos", []))
        emitted = 0
        for path in sorted((run / "callgraph").glob("*.jsonl")):
            emitted += sum(1 for line in path.read_text("utf-8").splitlines()
                           if line.strip())
        consumed = int(coverage.get("symbols_and_calls", {}).get(
            "counts", {}).get("call_edges", 0))
        check("callgraph-producer-shape", emitted <= declared,
              f"declared={declared}, canonical-jsonl={emitted}")
        check("callgraph-consumption", emitted == 0 or consumed > 0,
              f"canonical-jsonl={emitted}, system-model call_edges={consumed}")

    dep_cov_path = run / "imports" / "depmap-coverage.json"
    if dep_cov_path.is_file():
        dep_cov = _load(dep_cov_path)
        invalid_complete = [row for row in dep_cov.get("repos", [])
                            if row.get("status") == "complete" and not row.get("map_file")]
        mapped_rows = [row for row in dep_cov.get("repos", []) if row.get("map_file")]
        missing_maps = [row["map_file"] for row in mapped_rows
                        if not (run / "imports" / row["map_file"]).is_file()]
        mapped_repos = len({row.get("repository_ref", "") for row in mapped_rows})
        consumed_repos = int(coverage.get("dependency_imports", {}).get(
            "counts", {}).get("repos_with_maps", 0))
        check("dependency-map-producer-shape", not missing_maps and not invalid_complete,
              "all complete rows declare existing maps"
              if not missing_maps and not invalid_complete else
              f"missing maps={missing_maps}; complete rows without maps="
              f"{[row.get('repository_ref', '') for row in invalid_complete]}")
        check("dependency-map-consumption", mapped_repos == consumed_repos,
              f"producer repos={mapped_repos}, system-model repos={consumed_repos}")

    route_inventory_path = run / "routes" / "route-inventory.json"
    route_rows = (_load(route_inventory_path) if route_inventory_path.is_file()
                 else {}).get("rows", [])
    route_nodes = int(coverage.get("routes", {}).get("counts", {}).get("routes", 0))
    check("route-liveness-consumption", not route_rows or route_nodes > 0,
          f"producer rows={len(route_rows)}, system-model routes={route_nodes}")

    candidate_doc = _load(run / "module-candidates.json")
    check("module-candidate-count",
          candidate_doc.get("candidate_count") == len(candidate_doc.get("candidates", [])),
          f"declared={candidate_doc.get('candidate_count')}, "
          f"rows={len(candidate_doc.get('candidates', []))}")
    for node_kind, signal_kind in (("route", "route"),
                                   ("route", "route-mount"),
                                   ("data-store", "data-store")):
        expected_ids = set()
        for node in model.get("nodes", []):
            if node.get("kind") != node_kind:
                continue
            is_mount = node.get("attrs", {}).get("registration_kind") == "mount"
            if node_kind == "route" and (signal_kind == "route-mount") != is_mount:
                continue
            expected_ids.add(str(node.get("id", "")))
        observed = []
        for candidate in candidate_doc.get("candidates", []):
            if candidate.get("signal_kind") == signal_kind:
                observed.extend(str(node_id) for node_id in candidate.get("node_ids", []))
        check(
            f"canonical-{signal_kind}-candidate-coverage",
            set(observed) == expected_ids and len(observed) == len(set(observed)),
            f"canonical_nodes={len(expected_ids)}, candidate_links={len(observed)}, "
            f"unique_links={len(set(observed))}",
        )
    if require_module_map or (run / "module-map.json").is_file():
        try:
            candidates, modules = module_map.validate(run)
            module_count = len(modules.get("modules", []))
            model_module_count = int(coverage.get("modules", {}).get(
                "counts", {}).get("modules", 0))
            expected_modules = {
                row["module_id"]: (row.get("name", ""), row.get("classification", ""),
                                   sorted(set(row.get("aliases", []))))
                for row in modules.get("modules", [])}
            observed_modules = {
                node.get("attrs", {}).get("module_id", ""):
                (node.get("label", ""), node.get("attrs", {}).get("classification", ""),
                 sorted(set(node.get("attrs", {}).get("aliases", []))))
                for node in model.get("nodes", []) if node.get("kind") == "module"}
            check("module-disposition-accounting", True,
                  f"{candidates.get('candidate_count', 0)} candidates dispositioned once")
            check("module-node-consumption",
                  module_count == model_module_count and expected_modules == observed_modules,
                  f"module-map={module_count}, system-model={model_module_count}; "
                  f"identities_match={expected_modules == observed_modules}")
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            check("module-disposition-accounting", False, str(exc))

    heads = {identities.reference_for(repo.repo_id): repo.git.head.lower()
             for repo in spec.repos if repo.git.head}
    citation_problems: list[str] = []
    artifact_names = ["system-model.json", "module-candidates.json", "module-map.json",
                      "module-summary.md"]
    if require_reports:
        artifact_names += ["findings.json", "findings-summary.md",
                           "findings-pm-summary.md", "project-map.md",
                           "technical-overview.md", "overview.md"]
    elif (run / "findings.json").is_file():
        artifact_names += ["findings.json", "findings-summary.md",
                           "findings-pm-summary.md"]
    for name in artifact_names:
        path = run / name
        if not path.is_file():
            optional_module_artifact = name in {"module-map.json", "module-summary.md"}
            if require_reports or require_module_map or not optional_module_artifact:
                citation_problems.append(f"missing {name}")
            continue
        text = path.read_text("utf-8", errors="replace")
        for repository_ref, expected in heads.items():
            citation = re.compile(
                rf"(?<![A-Za-z0-9._/%-]){re.escape(repository_ref)}@"
                r"([0-9a-fA-F]{7,40}):")
            for revision in citation.findall(text):
                if revision.lower() != expected:
                    citation_problems.append(
                        f"{name}: {repository_ref}@{revision} is not full recorded HEAD")
        for repo in spec.repos:
            if repo.path and repo.path in text:
                citation_problems.append(f"{name}: contains absolute target path")
    check("revision-and-path-citations", not citation_problems,
          "citations use full recorded revisions and no target absolute paths"
          if not citation_problems else "; ".join(citation_problems[:20]))

    leakage = []
    external_identities = [identities.project, *identities.repositories]
    evidence_files = [
        "discovery-report.json", "capabilities.json", "callgraph-coverage.json",
        "imports/depmap-coverage.json", "system-model.json",
        "module-candidates.json", "module-map.json", "workspace-metrics.json",
        "synthesis-input.json", "findings.json", "signals/run-summary.json",
    ]
    for rel in evidence_files:
        path = run / rel
        if not path.is_file():
            continue
        text = path.read_text("utf-8", errors="replace")
        for item in external_identities:
            if item.internal_id != item.reference and item.internal_id in text:
                leakage.append(f"{rel}: {item.internal_id}")
    for directory, suffixes in ((run / "signals", (".manifest.json", ".view.txt")),
                                (run / "callgraph", (".jsonl",)),
                                (run / "imports", (".depcruise.json", ".golist.json"))):
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if any(path.name.endswith(suffix) for suffix in suffixes):
                text = path.read_text("utf-8", errors="replace")
                for item in external_identities:
                    if item.internal_id != item.artifact_key \
                            and item.internal_id in path.name:
                        leakage.append(f"{path.relative_to(run)}: filename")
                    if item.internal_id != item.reference \
                            and item.internal_id in text:
                        leakage.append(f"{path.relative_to(run)}: content")
    for path in sorted(run.glob("*.md")):
        text = path.read_text("utf-8", errors="replace")
        for item in external_identities:
            if item.internal_id != item.reference and item.internal_id in text:
                leakage.append(f"{path.name}: content")
    check("external-identity-boundary", not leakage,
          "evidence, reports, and artifact filenames contain no internal repository IDs"
          if not leakage else "; ".join(leakage[:20]))

    if require_reports:
        technical = (run / "technical-overview.md").read_text(
            "utf-8", errors="replace") if (run / "technical-overview.md").is_file() else ""
        expected = coverage_render.render(run)
        observed = coverage_render.extract(technical)
        check("machine-capability-coverage", observed == expected,
              "technical report contains the exact wrapper-rendered capability block"
              if observed == expected else
              "technical report capability block is missing or differs from capabilities.json")
        project_map = (run / "project-map.md").read_text(
            "utf-8", errors="replace") if (run / "project-map.md").is_file() else ""
        expected_modules = module_render.render(run)
        observed_modules = module_render.extract(project_map)
        check("machine-module-map", observed_modules == expected_modules,
              "project map contains the exact validated module table"
              if observed_modules == expected_modules else
              "project map module block is missing or differs from module-map.json")

        try:
            findings.validate(run)
            expected_technical_findings = findings.render_technical(run)
            expected_pm_findings = findings.render_pm(run)
            finding_error = ""
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            expected_technical_findings = expected_pm_findings = ""
            finding_error = str(exc)
        observed_technical_findings = findings.extract_technical(technical)
        check("machine-verified-technical-findings",
              not finding_error and observed_technical_findings == expected_technical_findings,
              "technical report contains the exact validated atomic findings block"
              if not finding_error and observed_technical_findings == expected_technical_findings
              else finding_error or "technical findings block is missing or changed")

        overview = (run / "overview.md").read_text(
            "utf-8", errors="replace") if (run / "overview.md").is_file() else ""
        observed_pm_findings = findings.extract_pm(overview)
        check("machine-verified-pm-findings",
              not finding_error and observed_pm_findings == expected_pm_findings,
              "PM report contains the exact verified findings projection"
              if not finding_error and observed_pm_findings == expected_pm_findings
              else finding_error or "PM findings block is missing or changed")
        entities = sorted(set(_HTML_ENTITY.findall(overview)))
        encoded_links = _ENCODED_LOCAL_LINK.findall(overview)
        check("pm-text-integrity", not entities and not encoded_links,
              "PM overview uses plain Unicode/Markdown text"
              if not entities and not encoded_links else
              (("HTML entities are forbidden in PM Markdown: "
                + ", ".join(entities[:20])) if entities else
               "percent-encoded local Markdown links are forbidden"))
        mermaid_problems = (
            _mermaid_integrity_problems("overview.md", overview)
            + _mermaid_integrity_problems("project-map.md", project_map)
            + _mermaid_integrity_problems("technical-overview.md", technical)
        )
        check("mermaid-text-integrity", not mermaid_problems,
              "Mermaid blocks use unencoded standard edge punctuation"
              if not mermaid_problems else "; ".join(mermaid_problems[:20]))
        minutes = _pm_reading_minutes(overview)
        check("pm-reading-budget", minutes <= 10.5,
              f"estimated prose reading time={minutes:.1f} minutes (limit 10.5)")
        pm_leaks = []
        if _has_source_citation(overview, identities):
            pm_leaks.append("source citation present")
        file_labels = {str(node.get("label", "")) for node in model.get("nodes", [])
                       if node.get("kind") == "file"
                       and _is_source_path_label(str(node.get("label", "")))}
        for label in sorted(file_labels):
            # Match the exact mechanically observed path, regardless of
            # Markdown quoting. File-node labels that are only package names or
            # ordinary words are deliberately excluded because exact substring
            # matching would turn the abstraction gate into a prose-word gate.
            if label in overview:
                pm_leaks.append(f"source path present: {label}")
                if len(pm_leaks) >= 20:
                    break
        signal_summary = _load(run / "signals" / "run-summary.json")
        tool_names = {str(row.get("tool", ""))
                      for row in signal_summary.get("signals", []) if row.get("tool")}
        for tool in sorted(tool_names):
            # Tool names can be ordinary words (for example, "coverage").
            # Restrict this gate to explicit code-style identifiers instead of
            # inferring jargon from natural-language prose.
            if f"`{tool}`" in overview:
                pm_leaks.append(f"tool identifier present: {tool}")
        check("pm-abstraction-boundary", not pm_leaks,
              "PM overview contains no source citations/paths/tool identifiers"
              if not pm_leaks else "; ".join(pm_leaks[:20]))

        if language != "en":
            # (b) Missing disclaimer: SKILL.md requires every report to carry
            # a faithful translation of the standing scope disclaimer in the
            # run language -- not the English original. Absence in ANY of the
            # three narrative reports is a leakage failure.
            marker = _DISCLAIMER_MARKERS.get(language)
            reports = (("technical-overview.md", technical),
                       ("overview.md", overview),
                       ("project-map.md", project_map))
            if marker is None:
                check("language-standing-disclaimer", False,
                      f"no standing-disclaimer marker registered for "
                      f"language {language!r} in overview_audit."
                      f"_DISCLAIMER_MARKERS; add one before delivering this "
                      f"language")
            else:
                disclaimer_problems = [name for name, text in reports
                                       if text and marker not in text]
                check(
                    "language-standing-disclaimer", not disclaimer_problems,
                    "standing scope disclaimer present (in the run language) "
                    "in every present narrative report"
                    if not disclaimer_problems else
                    f"standing scope disclaimer missing (marker {marker!r} "
                    f"not found) in: " + ", ".join(disclaimer_problems),
                )

            # (c) Stray untranslated prose: a low-false-positive heuristic,
            # not a hard failure (see module docstring note above _stray_
            # english_prose) -- a false failure on a good run is worse than a
            # missed heuristic, so this is WARNING-only and never contributes
            # to `failed_count`/`status`.
            prose_warnings = []
            for name, text in reports:
                if not text:
                    continue
                hits = _stray_english_prose(text)
                if hits:
                    prose_warnings.append(f"{name}: " + "; ".join(hits[:3]))
            warn(
                "language-stray-english-prose", bool(prose_warnings),
                "no sentence-like English prose runs detected outside "
                "verbatim-cited tokens"
                if not prose_warnings else
                "possible untranslated English prose (heuristic, "
                "non-blocking -- verify by hand): " + " | ".join(prose_warnings),
            )

    failed = [row for row in checks if row["status"] == "fail"]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "failed" if failed else "passed",
        "failed_count": len(failed),
        "checks": checks,
    }


def write(run_dir: str | Path, *, require_module_map: bool = False,
          require_reports: bool = False) -> Path:
    run = Path(run_dir).expanduser().resolve()
    out = run / "consistency-audit.json"
    replace_artifact_text(out, sanitize_text(json.dumps(
        audit(run, require_module_map=require_module_map,
              require_reports=require_reports),
        indent=2, sort_keys=True) + "\n"))
    return out

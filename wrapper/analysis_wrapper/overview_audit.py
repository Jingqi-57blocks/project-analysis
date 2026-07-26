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

from . import (coverage_render, findings, identity, module_map, module_render,
               synthesis_input, workspace_metrics)
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

# Machine-artifact directories the wrapper writes into under a run dir
# (57B-112 §1): the external-identity-boundary check below walks every one of
# these RECURSIVELY, not just the pre-migration three (signals/callgraph/
# imports), since a later migration (57B-80/57B-82/57B-84) added datastore/,
# deploy/, access/, integrations/, and routes/ as further per-repo evidence
# homes — each one just as capable of accidentally embedding an internal
# repository id in a filename or file body as the original three. Recursive
# so nested fragment dirs (``.fragments/`` under callgraph/imports/routes,
# see those packages' own emit.py) are covered too, not just each root's
# immediate children. Deliberately a closed, current allowlist rather than a
# blanket walk of the whole run dir: it excludes dot-prefixed analyzer-owned
# working dirs that sit alongside these (e.g. ``.depmap-config/``) the same
# way ``signals/raw/`` is excluded below — neither is a shipped/consumed
# artifact, so leakage there is out of this check's scope.
ARTIFACT_ROOTS = ("signals", "callgraph", "imports", "routes",
                  "datastore", "deploy", "access", "integrations")


def _artifact_files(run: Path) -> list[Path]:
    """Every file under ``run``'s ``ARTIFACT_ROOTS``, recursively.

    ``signals/raw/`` is the one exclusion: a self-gitignored, owner-only
    containment zone that is never model-read or shipped anywhere (see
    ``executor._containment_dir``), so it is out of scope for a check about
    evidence that IS read/shipped. Deterministic order: roots are walked in
    ``ARTIFACT_ROOTS`` order, each root's files sorted by path.
    """
    raw_dir = run / "signals" / "raw"
    files: list[Path] = []
    for name in ARTIFACT_ROOTS:
        root = run / name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.is_relative_to(raw_dir):
                continue
            files.append(path)
    return files


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

    spec = TargetSpec.load(run / "targets.json")
    identities = identity.load(run)
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
    for path in _artifact_files(run):
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

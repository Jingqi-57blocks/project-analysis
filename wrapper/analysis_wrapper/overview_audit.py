"""Generic cross-stage consistency audit for overview artifacts.

The audit compares structured producer declarations with structured consumer
counts.  It intentionally does not inspect business words in narrative output;
semantic interpretation remains bounded by evidence-basis rules in synthesis.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import coverage_render, module_map, module_render
from .executor import replace_artifact_text
from .sanitize import sanitize_text
from .targetspec import TargetSpec

SCHEMA_VERSION = "1.0.0"
_CITATION = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)@([0-9a-fA-F]{7,40}):")
_FENCE = re.compile(r"```.*?```", re.S)
_HTML_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WORD = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9'-]*\b")


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


def audit(run_dir: str | Path, *, require_module_map: bool = False,
          require_reports: bool = False) -> dict:
    run = Path(run_dir).expanduser().resolve()
    checks: list[dict] = []

    def check(code: str, passed: bool, detail: str) -> None:
        checks.append({"check": code, "status": "pass" if passed else "fail",
                       "detail": detail})

    spec = TargetSpec.load(run / "targets.json")
    capabilities = _load(run / "capabilities.json")
    model = _load(run / "system-model.json")
    coverage = model.get("coverage", {})

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
                    f"{row.get('repo_id', '')}/{row.get('tool', '')}: valid view missing")
            manifest_name = str(row.get("manifest", ""))
            manifests = [run / "signals" / manifest_name] if manifest_name else []
            if len(manifests) != 1 or not manifests[0].is_file():
                signal_mismatches.append(
                    f"{row.get('repo_id', '')}/{row.get('tool', '')}: manifest missing")
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
        if cap_state == "not-applicable":
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
        mapped_repos = len({row.get("repo_id", "") for row in mapped_rows})
        consumed_repos = int(coverage.get("dependency_imports", {}).get(
            "counts", {}).get("repos_with_maps", 0))
        check("dependency-map-producer-shape", not missing_maps and not invalid_complete,
              "all complete rows declare existing maps"
              if not missing_maps and not invalid_complete else
              f"missing maps={missing_maps}; complete rows without maps="
              f"{[row.get('repo_id', '') for row in invalid_complete]}")
        check("dependency-map-consumption", mapped_repos == consumed_repos,
              f"producer repos={mapped_repos}, system-model repos={consumed_repos}")

    report = _load(run / "discovery-report.json")
    route_rows = (report.get("route_liveness") or {}).get("rows", [])
    route_nodes = int(coverage.get("routes", {}).get("counts", {}).get("routes", 0))
    check("route-liveness-consumption", not route_rows or route_nodes > 0,
          f"producer rows={len(route_rows)}, system-model routes={route_nodes}")

    candidate_doc = _load(run / "module-candidates.json")
    check("module-candidate-count",
          candidate_doc.get("candidate_count") == len(candidate_doc.get("candidates", [])),
          f"declared={candidate_doc.get('candidate_count')}, "
          f"rows={len(candidate_doc.get('candidates', []))}")
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

    heads = {repo.repo_id: repo.git.head.lower() for repo in spec.repos if repo.git.head}
    citation_problems: list[str] = []
    artifact_names = ["system-model.json", "module-candidates.json", "module-map.json",
                      "module-summary.md"]
    if require_reports:
        artifact_names += ["project-map.md", "technical-overview.md", "overview.md"]
    for name in artifact_names:
        path = run / name
        if not path.is_file():
            optional_module_artifact = name in {"module-map.json", "module-summary.md"}
            if require_reports or require_module_map or not optional_module_artifact:
                citation_problems.append(f"missing {name}")
            continue
        text = path.read_text("utf-8", errors="replace")
        for repo_id, revision in _CITATION.findall(text):
            expected = heads.get(repo_id)
            if expected and revision.lower() != expected:
                citation_problems.append(
                    f"{name}: {repo_id}@{revision} is not full recorded HEAD")
        for repo in spec.repos:
            if repo.path and repo.path in text:
                citation_problems.append(f"{name}: contains absolute target path")
    check("revision-and-path-citations", not citation_problems,
          "citations use full recorded revisions and no target absolute paths"
          if not citation_problems else "; ".join(citation_problems[:20]))

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

        overview = (run / "overview.md").read_text(
            "utf-8", errors="replace") if (run / "overview.md").is_file() else ""
        entities = sorted(set(_HTML_ENTITY.findall(overview)))
        check("pm-text-integrity", not entities,
              "PM overview uses plain Unicode/Markdown text"
              if not entities else
              "HTML entities are forbidden in PM Markdown: " + ", ".join(entities[:20]))
        minutes = _pm_reading_minutes(overview)
        check("pm-reading-budget", minutes <= 10.5,
              f"estimated prose reading time={minutes:.1f} minutes (limit 10.5)")
        pm_leaks = []
        if _CITATION.search(overview):
            pm_leaks.append("source citation present")
        file_labels = {str(node.get("label", "")) for node in model.get("nodes", [])
                       if node.get("kind") == "file"
                       and node.get("label") not in {None, "", "(unknown)"}}
        for label in sorted(file_labels):
            # Match the exact mechanically observed path, regardless of
            # Markdown quoting.  Limiting the candidate set to file-node labels
            # avoids generic source-code keyword heuristics while still catching
            # plain-text paths such as src/client.ts.
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

"""57B-79: the module-scoped evidence view, built from synthetic inputs only.

No real capability provider is wired in yet, so this exercises module_view
against a minimal, hand-built SystemModel-shaped dict (a module node plus
containment edges to a couple of owned nodes) and a couple of synthetic
CapabilityResults — exactly the kind of input a live pipeline would produce
once 78/80-84 land real providers.
"""

from analysis_wrapper.evidence import Coverage, Fact, SourceRef
from analysis_wrapper.evidence import module_view
from analysis_wrapper.profiles.contracts import CapabilityResult


def _coverage(**overrides):
    fields = {"applicability": "applicable", "status": "complete",
              "reason_code": "ok", "detail": ""}
    fields.update(overrides)
    return Coverage(**fields)


def _model():
    return {
        "nodes": [
            {"id": "mod:1", "kind": "module", "label": "Billing",
             "attrs": {"classification": "business"}},
            {"id": "route:1", "kind": "route", "label": "GET /invoices",
             "repository_ref": "api"},
            {"id": "file:1", "kind": "file", "label": "internal/billing.go",
             "repository_ref": "api"},
            {"id": "route:2", "kind": "route", "label": "GET /health",
             "repository_ref": "worker"},
        ],
        "edges": [
            {"type": "containment", "src": "mod:1", "dst": "route:1"},
            {"type": "containment", "src": "mod:1", "dst": "file:1"},
        ],
    }


def _result(repo_id, capability_id, facts):
    return CapabilityResult(
        capability_id=capability_id, provider_id="synthetic-provider", repo_id=repo_id,
        coverage=_coverage(), facts=facts)


def test_module_view_links_facts_by_repository_scope(tmp_path):
    ref = SourceRef(repository_ref="api", revision="a" * 40,
                    path="internal/billing.go", line=4)
    linked_fact = Fact(fact_id="fact:linked", kind="route",
                       data={"path": "/invoices"}, source_refs=(ref,))
    unrelated_ref = SourceRef(repository_ref="worker", revision="b" * 40,
                              path="worker/main.go", line=1)
    unrelated_fact = Fact(fact_id="fact:unrelated", kind="deployment",
                          data={}, source_refs=(unrelated_ref,))
    results = [
        _result("api-repo-id", "route-inventory", (linked_fact,)),
        _result("worker-repo-id", "deploy-units", (unrelated_fact,)),
    ]

    document = module_view.build(_model(), results)

    assert len(document["modules"]) == 1
    module = document["modules"][0]
    assert module["module_id"] == "mod:1"
    assert module["name"] == "Billing"
    assert module["classification"] == "business"
    assert module["facts"]["total_count"] == 1
    assert module["facts"]["items"][0]["fact_id"] == "fact:linked"


def test_module_view_groups_evidence_by_fact_kind():
    api_ref = SourceRef(repository_ref="api", revision="a" * 40, path="a.go", line=1)
    route_fact = Fact(fact_id="fact:route", kind="route", data={}, source_refs=(api_ref,))
    data_fact = Fact(fact_id="fact:data", kind="data-store", data={}, source_refs=(api_ref,))
    results = [_result("api-repo-id", "route-inventory", (route_fact, data_fact))]

    document = module_view.build(_model(), results)

    grouped = document["modules"][0]["evidence_by_kind"]
    assert set(grouped) == {"route", "data-store"}
    assert grouped["route"]["items"][0]["fact_id"] == "fact:route"


def test_module_view_aggregates_coverage_without_masking_a_worse_facet():
    api_ref = SourceRef(repository_ref="api", revision="a" * 40, path="a.go", line=1)
    complete_fact = Fact(fact_id="fact:1", kind="route", data={}, source_refs=(api_ref,))
    partial_fact = Fact(fact_id="fact:2", kind="data-store", data={}, source_refs=(api_ref,))
    results = [
        _result("api-repo-id", "route-inventory", (complete_fact,)),
        CapabilityResult(
            capability_id="data-model", provider_id="synthetic-provider",
            repo_id="api-repo-id",
            coverage=_coverage(status="partial", reason_code="cap-hit",
                               detail="evidence capped"),
            facts=(partial_fact,)),
    ]

    document = module_view.build(_model(), results)

    assert document["modules"][0]["coverage"]["status"] == "partial"


def test_module_view_reports_unknown_coverage_when_no_evidence_links():
    document = module_view.build(_model(), [])
    coverage = document["modules"][0]["coverage"]
    assert coverage["applicability"] == "unknown"
    assert coverage["status"] == "unavailable"


def test_module_view_is_deterministic_and_bounds_large_fact_lists():
    api_ref = SourceRef(repository_ref="api", revision="a" * 40, path="a.go", line=1)
    many_facts = tuple(
        Fact(fact_id=f"fact:{index:04d}", kind="route", data={}, source_refs=(api_ref,))
        for index in range(250)
    )
    results = [_result("api-repo-id", "route-inventory", many_facts)]

    first = module_view.build(_model(), results)
    second = module_view.build(_model(), results)

    assert first == second
    facts_view = first["modules"][0]["facts"]
    assert facts_view["total_count"] == 250
    assert facts_view["included_count"] == 200
    assert facts_view["truncated"] is True

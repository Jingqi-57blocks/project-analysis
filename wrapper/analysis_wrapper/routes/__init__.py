"""Route inventory + UI-route liveness (57B-84 B2).

Two zero-profile universal capability providers (``profiles/providers.py``'s
``RouteInventoryProvider``/``UiRouteLinkageProvider``) replicate discovery's
legacy backend/frontend gates per-repo and write ONE fragment each — a
backend's route registrations, or a frontend's raw UI call sites — under
``routes/.fragments/``. ``emit`` is the technology-neutral second half: it
reads every fragment post-provider-loop and performs the cross-repo join
(base->backend resolution + call/route matching) that used to happen inside
``discovery.liveness.liveness()``, called once per frontend and internally
RE-SCANNING every backend's routes each time. The join logic now runs once
against the providers' already-scanned fragments instead.
"""

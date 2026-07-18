"""Offline, data-driven HTML report generator (57B-35).

A DETERMINISTIC renderer: it reads a *completed* Project Analysis run directory
and emits a self-contained static report folder (``index.html`` + locally
bundled assets) that opens over ``file://`` with no server, no network, and no
LLM calls. The report is built from the run's structured artifacts; canonical
Markdown is used only for narrative sections and lossless full-document views.

Entry point::

    from analysis_wrapper.report_html.generate import generate
    result = generate(run_dir)
"""

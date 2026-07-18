/* Project Analysis offline report — presentation glue only.
   No network, no dependencies beyond the vendored mermaid runtime.
   Everything degrades gracefully if mermaid is unavailable. Theme is light. */
(function () {
  "use strict";

  /* ---- mermaid ---- */
  var sources = [];
  function collectSources() {
    var nodes = document.querySelectorAll("pre.mermaid");
    for (var i = 0; i < nodes.length; i++) {
      sources[i] = nodes[i].textContent;
      nodes[i].setAttribute("data-mermaid-index", String(i));
    }
  }
  function renderDiagrams() {
    if (typeof window.mermaid === "undefined") { return; }
    var nodes = document.querySelectorAll("pre.mermaid");
    for (var i = 0; i < nodes.length; i++) {
      var idx = nodes[i].getAttribute("data-mermaid-index");
      if (idx !== null && sources[idx] !== undefined) {
        nodes[i].textContent = sources[idx];   // restore source before re-render
      }
      nodes[i].removeAttribute("data-processed");
    }
    try {
      window.mermaid.initialize({
        startOnLoad: false,
        securityLevel: "antiscript",
        theme: "default"
      });
      window.mermaid.run({ querySelector: "pre.mermaid" });
    } catch (e) { /* leave the source visible on failure */ }
  }

  /* ---- diagram zoom ---- */
  function bindZoom() {
    var groups = document.querySelectorAll(".diagram");
    groups.forEach(function (fig) {
      var target = fig.querySelector("pre.mermaid");
      var scale = { v: 1 };
      function apply() { if (target) { target.style.transform = "scale(" + scale.v + ")"; } }
      fig.querySelectorAll(".zoom-in").forEach(function (b) {
        b.addEventListener("click", function () { scale.v = Math.min(3, scale.v + 0.2); apply(); });
      });
      fig.querySelectorAll(".zoom-out").forEach(function (b) {
        b.addEventListener("click", function () { scale.v = Math.max(0.4, scale.v - 0.2); apply(); });
      });
      fig.querySelectorAll(".zoom-reset").forEach(function (b) {
        b.addEventListener("click", function () { scale.v = 1; apply(); });
      });
    });
  }

  /* ---- search / filter ---- */
  function bindFilters() {
    var inputs = document.querySelectorAll(".filter-input");
    inputs.forEach(function (input) {
      var targetId = input.getAttribute("data-filter-target");
      var container = targetId ? document.getElementById(targetId) : null;
      if (!container) { return; }
      input.addEventListener("input", function () {
        var q = input.value.trim().toLowerCase();
        var items = container.querySelectorAll("[data-search], tbody tr");
        items.forEach(function (el) {
          var hay = (el.getAttribute("data-search") || el.textContent || "").toLowerCase();
          el.classList.toggle("is-hidden", q !== "" && hay.indexOf(q) === -1);
        });
      });
    });
  }

  /* ---- floating TOC drawer: click the handle to open/close (hover peeks via CSS) ---- */
  function bindTocDrawer() {
    document.querySelectorAll(".toc-drawer .toc-handle").forEach(function (btn) {
      var drawer = btn.closest(".toc-drawer");
      if (!drawer) { return; }
      btn.addEventListener("click", function () {
        var closed = drawer.classList.toggle("toc-closed");
        btn.setAttribute("aria-expanded", String(!closed));
      });
    });
  }

  /* ---- init ---- */
  function init() {
    collectSources();
    renderDiagrams();
    bindZoom();
    bindFilters();
    bindTocDrawer();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();

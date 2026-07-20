/* Project Analysis offline report — presentation glue only.
   No network, no dependencies beyond the vendored mermaid runtime.
   Everything degrades gracefully if mermaid is unavailable. Theme is light. */
(function () {
  "use strict";

  /* ---- mermaid ---- */
  var sources = [];
  var renderPromise = Promise.resolve();
  function collectSources() {
    var nodes = document.querySelectorAll("pre.mermaid");
    for (var i = 0; i < nodes.length; i++) {
      sources[i] = nodes[i].textContent;
      nodes[i].setAttribute("data-mermaid-index", String(i));
    }
  }
  function renderDiagrams() {
    if (typeof window.mermaid === "undefined") { return renderPromise; }
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
      renderPromise = Promise.resolve(
        window.mermaid.run({ querySelector: "pre.mermaid" })
      ).catch(function () { /* leave the source visible on failure */ });
    } catch (e) {
      renderPromise = Promise.resolve(); /* leave the source visible on failure */
    }
    return renderPromise;
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

  /* ---- independent large diagram dialog ----
     Re-render from the stored Mermaid source so SVG ids remain unique. Modal
     zoom changes the SVG's layout dimensions (not a clipped CSS transform), so
     the scroll area always contains the entire enlarged diagram. */
  function bindDiagramDialogs() {
    var openers = document.querySelectorAll(".diagram-expand");
    if (!openers.length) { return; }

    var dialog = document.createElement("dialog");
    dialog.className = "diagram-modal";
    dialog.setAttribute("aria-label", "Large diagram view");
    dialog.innerHTML =
      '<div class="diagram-modal-shell">' +
        '<div class="diagram-modal-toolbar">' +
          '<strong>Large diagram</strong>' +
          '<span class="diagram-modal-actions">' +
            '<button type="button" class="modal-zoom-out" aria-label="zoom out">-</button>' +
            '<button type="button" class="modal-zoom-reset" aria-label="reset zoom">100%</button>' +
            '<button type="button" class="modal-zoom-in" aria-label="zoom in">+</button>' +
            '<button type="button" class="modal-zoom-fit">fit</button>' +
            '<button type="button" class="diagram-modal-close" aria-label="close large diagram">×</button>' +
          '</span>' +
        '</div>' +
        '<div class="diagram-modal-scroll" tabindex="0">' +
          '<div class="diagram-modal-content"></div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(dialog);

    var content = dialog.querySelector(".diagram-modal-content");
    var scroll = dialog.querySelector(".diagram-modal-scroll");
    var resetButton = dialog.querySelector(".modal-zoom-reset");
    var closeButton = dialog.querySelector(".diagram-modal-close");
    var activeOpener = null;
    var scale = 1;
    var baseWidth = 0;
    var baseHeight = 0;
    var renderSequence = 0;

    function svgSize(svg) {
      var viewBox = (svg.getAttribute("viewBox") || "").trim().split(/[ ,]+/);
      var width = viewBox.length === 4 ? Number(viewBox[2]) : 0;
      var height = viewBox.length === 4 ? Number(viewBox[3]) : 0;
      if (!(width > 0 && height > 0)) {
        var rect = svg.getBoundingClientRect();
        width = rect.width || 800;
        height = rect.height || 600;
      }
      return { width: width, height: height };
    }

    function applyModalScale() {
      var svg = content.querySelector("svg");
      if (!svg || !(baseWidth > 0 && baseHeight > 0)) { return; }
      svg.style.maxWidth = "none";
      svg.style.width = (baseWidth * scale) + "px";
      svg.style.height = (baseHeight * scale) + "px";
      resetButton.textContent = Math.round(scale * 100) + "%";
    }

    function prepareSvg() {
      var svg = content.querySelector("svg");
      if (!svg) { return; }
      var size = svgSize(svg);
      baseWidth = size.width;
      baseHeight = size.height;
      scale = 1;
      applyModalScale();
    }

    function showDialog(opener) {
      activeOpener = opener;
      opener.setAttribute("aria-expanded", "true");
      document.body.classList.add("diagram-modal-open");
      if (!dialog.open) { dialog.showModal(); }
      prepareSvg();
      scroll.scrollTop = 0;
      scroll.scrollLeft = 0;
      closeButton.focus();
    }

    function sourceFallback(source, opener) {
      content.textContent = source;
      content.classList.add("diagram-modal-source");
      scale = 1;
      baseWidth = 0;
      baseHeight = 0;
      resetButton.textContent = "100%";
      showDialog(opener);
    }

    function openDiagram(opener) {
      var figure = opener.closest(".diagram");
      var target = figure ? figure.querySelector("pre.mermaid") : null;
      var idx = target ? target.getAttribute("data-mermaid-index") : null;
      var source = idx !== null ? sources[idx] : "";
      content.classList.remove("diagram-modal-source");
      content.textContent = "";

      if (!source || typeof window.mermaid === "undefined" ||
          typeof window.mermaid.render !== "function") {
        sourceFallback(source || (target ? target.textContent : ""), opener);
        return;
      }
      renderSequence += 1;
      Promise.resolve(
        window.mermaid.render("large-diagram-" + renderSequence, source)
      ).then(function (rendered) {
        content.innerHTML = rendered.svg;
        if (typeof rendered.bindFunctions === "function") {
          rendered.bindFunctions(content);
        }
        showDialog(opener);
      }).catch(function () { sourceFallback(source, opener); });
    }

    openers.forEach(function (button) {
      button.setAttribute("aria-expanded", "false");
      button.addEventListener("click", function () {
        renderPromise.then(function () { openDiagram(button); });
      });
    });

    dialog.querySelector(".modal-zoom-out").addEventListener("click", function () {
      scale = Math.max(0.2, scale - 0.2);
      applyModalScale();
    });
    dialog.querySelector(".modal-zoom-in").addEventListener("click", function () {
      scale = Math.min(4, scale + 0.2);
      applyModalScale();
    });
    resetButton.addEventListener("click", function () {
      scale = 1;
      applyModalScale();
    });
    dialog.querySelector(".modal-zoom-fit").addEventListener("click", function () {
      if (!(baseWidth > 0 && baseHeight > 0)) { return; }
      var availableWidth = Math.max(1, scroll.clientWidth - 48);
      var availableHeight = Math.max(1, scroll.clientHeight - 48);
      scale = Math.max(0.2, Math.min(4,
        availableWidth / baseWidth, availableHeight / baseHeight));
      applyModalScale();
      scroll.scrollTop = 0;
      scroll.scrollLeft = 0;
    });
    closeButton.addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) { dialog.close(); }
    });
    dialog.addEventListener("close", function () {
      document.body.classList.remove("diagram-modal-open");
      content.textContent = "";
      if (activeOpener) {
        activeOpener.setAttribute("aria-expanded", "false");
        activeOpener.focus();
      }
      activeOpener = null;
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

  /* ---- floating TOC drawer ----
     Default closed = only the menu button. Mouse opens on hover (CSS); for
     touch/keyboard, clicking the button pins it open (.toc-open) and a tap
     outside closes it. Open = only the TOC, button hidden. */
  function bindTocDrawer() {
    document.querySelectorAll(".toc-drawer .toc-handle").forEach(function (btn) {
      var drawer = btn.closest(".toc-drawer");
      if (!drawer) { return; }
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var open = drawer.classList.toggle("toc-open");
        btn.setAttribute("aria-expanded", String(open));
      });
    });
    document.addEventListener("click", function (e) {
      document.querySelectorAll(".toc-drawer.toc-open").forEach(function (d) {
        if (!d.contains(e.target)) {
          d.classList.remove("toc-open");
          var h = d.querySelector(".toc-handle");
          if (h) { h.setAttribute("aria-expanded", "false"); }
        }
      });
    });
  }

  /* ---- init ---- */
  function init() {
    collectSources();
    renderDiagrams();
    bindZoom();
    bindDiagramDialogs();
    bindFilters();
    bindTocDrawer();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();

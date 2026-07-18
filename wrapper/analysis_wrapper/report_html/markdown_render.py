"""Canonical-Markdown rendering via markdown-it-py (the OSS renderer).

Responsibilities, all deterministic:

* Render a full document to HTML *losslessly* — every heading, paragraph, list,
  table, code block, diagram, link and inline stays present.
* Assign stable, unique heading anchors once, shared by the full-document view,
  the section-aware narrative slices and the content map (so all three agree).
* Locate fenced ``mermaid`` blocks and render their source verbatim into a
  ``<pre class="mermaid">`` figure (client-side mermaid renders it offline).
* Fall back to safe literal rendering for anything unsupported (``html=False``
  keeps raw HTML from source escaped rather than injected).

No business semantics are extracted from prose here: headings identify document
structure, nothing more.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .htmlutil import SlugAllocator, esc

try:  # pragma: no cover - import guard exercised via CLI, not unit tests
    from markdown_it import MarkdownIt
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "the HTML report needs the 'report' extra: install with "
        "`pip install -e .[report]` (or run bootstrap, which includes it)."
    ) from exc


@dataclass(frozen=True)
class Section:
    level: int          # 1..6
    text: str           # verbatim heading source text
    anchor: str         # unique slug, matches the rendered id=
    tok_start: int      # index of heading_open token
    tok_end: int        # exclusive end of this section's token slice


@dataclass(frozen=True)
class MermaidBlock:
    index: int          # 0-based order within the document
    source: str         # verbatim mermaid source
    diagram_type: str   # first source keyword (graph/flowchart/sequenceDiagram…)
    source_sha256: str
    nearest_anchor: str | None   # enclosing section anchor, if any


@dataclass
class MarkdownDoc:
    doc_id: str
    html: str
    sections: list[Section]
    mermaid_blocks: list[MermaidBlock]
    _tokens: list
    _md: "MarkdownIt"

    def section_html(self, anchor: str) -> str | None:
        """Render one section (its heading + body, including nested subsections)."""
        for sec in self.sections:
            if sec.anchor == anchor:
                slice_ = self._tokens[sec.tok_start:sec.tok_end]
                return self._md.renderer.render(slice_, self._md.options, {})
        return None

    def section_body_html(self, anchor: str) -> str | None:
        """Render one section's body only (skipping its own heading tokens)."""
        for sec in self.sections:
            if sec.anchor == anchor:
                start = sec.tok_start
                # heading is heading_open / inline / heading_close -> skip 3.
                body = self._tokens[start + 3:sec.tok_end]
                return self._md.renderer.render(body, self._md.options, {})
        return None


def mermaid_figure(source: str, dom_id: str = "") -> str:
    """Wrap verbatim mermaid source in a zoomable figure (rendered client-side)."""
    data = f' data-diagram="{dom_id}"' if dom_id else ""
    return (
        '<figure class="diagram"><div class="diagram-tools"' + data + ">"
        '<button type="button" class="zoom-out" aria-label="zoom out">-</button>'
        '<button type="button" class="zoom-reset" aria-label="reset zoom">reset</button>'
        '<button type="button" class="zoom-in" aria-label="zoom in">+</button>'
        "</div>"
        '<div class="diagram-scroll">'
        f'<pre class="mermaid">{esc(source)}</pre>'
        "</div></figure>"
    )


def _build_md() -> "MarkdownIt":
    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    md.enable(["table", "strikethrough"])
    md.renderer.rules["fence"] = _render_fence
    md.renderer.rules["table_open"] = _render_table_open
    md.renderer.rules["table_close"] = _render_table_close
    return md


def _render_table_open(tokens, idx, options, env) -> str:
    return '<div class="table-scroll"><table>'


def _render_table_close(tokens, idx, options, env) -> str:
    return "</table></div>"


def _render_fence(tokens, idx, options, env) -> str:
    token = tokens[idx]
    info = (token.info or "").strip()
    lang = info.split()[0] if info else ""
    if lang == "mermaid":
        n = env.setdefault("_mermaid_seq", 0)
        env["_mermaid_seq"] = n + 1
        return mermaid_figure(token.content, dom_id=str(n))
    # Non-mermaid fence: plain, escaped code block (safe literal fallback).
    cls = f' class="language-{esc(lang)}"' if lang else ""
    return f"<pre><code{cls}>{esc(token.content)}</code></pre>\n"


def _first_keyword(source: str) -> str:
    for line in source.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.split()[0] if stripped.split() else "diagram"
    return "diagram"


def _annotate(tokens: list, allocator: SlugAllocator) -> tuple[list[Section], list[MermaidBlock]]:
    """Attach unique ids to heading_open tokens; index sections and mermaid blocks."""
    heading_positions: list[tuple[int, int, str, str]] = []  # (tok_idx, level, text, anchor)
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            level = int(tok.tag[1])
            text = tokens[i + 1].content if i + 1 < len(tokens) else ""
            anchor = allocator.allocate(text or "section")
            tok.attrSet("id", anchor)
            tok.attrJoin("class", "doc-h")
            heading_positions.append((i, level, text, anchor))

    sections: list[Section] = []
    for pos, (tok_idx, level, text, anchor) in enumerate(heading_positions):
        end = len(tokens)
        for later_idx, later_level, _, _ in heading_positions[pos + 1:]:
            if later_level <= level:
                end = later_idx
                break
        sections.append(Section(level, text, anchor, tok_idx, end))

    mermaid_blocks: list[MermaidBlock] = []
    seq = 0
    for i, tok in enumerate(tokens):
        if tok.type == "fence" and (tok.info or "").strip().split()[:1] == ["mermaid"]:
            source = tok.content
            enclosing = None
            for sec in sections:
                if sec.tok_start < i < sec.tok_end:
                    enclosing = sec.anchor  # innermost wins (later, deeper sections)
            mermaid_blocks.append(
                MermaidBlock(
                    index=seq,
                    source=source,
                    diagram_type=_first_keyword(source),
                    source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
                    nearest_anchor=enclosing,
                )
            )
            seq += 1
    return sections, mermaid_blocks


def render_document(doc_id: str, text: str) -> MarkdownDoc:
    """Parse and render a canonical Markdown document losslessly."""
    md = _build_md()
    tokens = md.parse(text)
    allocator = SlugAllocator()
    sections, mermaid_blocks = _annotate(tokens, allocator)
    html = md.renderer.render(tokens, md.options, {})
    return MarkdownDoc(
        doc_id=doc_id,
        html=html,
        sections=sections,
        mermaid_blocks=mermaid_blocks,
        _tokens=tokens,
        _md=md,
    )

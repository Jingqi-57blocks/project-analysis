# Third-party notices

Project Analysis (this skill) is distributed under the MIT License (see `LICENSE`). It
installs, vendors, or invokes the third-party components below. This inventory follows
the machine-readable toolchain list in [`tools/manifest.json`](tools/manifest.json);
consult that file for ownership, version pins, and platform support.

Licenses were checked against each project's own repository. Anything not independently
confirmed is marked "to verify" rather than guessed.

## 1. Python packages installed by the wrapper (analyzer-managed)

Installed into the wrapper's own virtual environment by
`python3 -m analysis_wrapper.bootstrap` (`wrapper/pyproject.toml` optional-dependency
extras). Never installed into, or resolved from, a target repository.

| Package | Version pin | License | Role |
| --- | --- | --- | --- |
| [PyDriller](https://github.com/ishepard/pydriller) | `==2.10` (`history` extra) | Apache License 2.0 | Git history mining — commit, ownership, and co-change evidence for the history lane. |
| [SQLGlot](https://github.com/tobymao/sqlglot) | `==30.12.0` (`sql` extra) | MIT | Parses raw SQL DDL for the table/schema lane. |
| [markdown-it-py](https://github.com/executablebooks/markdown-it-py) | `==3.0.0` (`report` extra) | MIT | Canonical Markdown → HTML AST used to render the offline HTML export. Markdown reports themselves do not depend on it. |

`pydriller`, `sqlglot`, and `markdown-it-py` are all optional extras: their absence
degrades the corresponding lane to disclosed reduced coverage rather than failing the run.

## 2. Vendored JavaScript (bundled verbatim into generated reports)

Shipped in `wrapper/analysis_wrapper/report_html/vendor/` and inlined into the offline
HTML export so it renders fully offline over `file://`. Inventory and checksums are
tracked in that directory's own `VENDOR.txt`.

| Asset | Version | License | Role |
| --- | --- | --- | --- |
| [mermaid](https://github.com/mermaid-js/mermaid) (`mermaid.min.js`, UMD build) | 11.4.1 | MIT (full text bundled alongside as `mermaid.LICENSE.txt`) | Renders authored Mermaid diagram blocks in the browser, offline, with no dynamic `import()`/chunk fetches. |

No other vendored JavaScript/CSS ships in the reports; `report_html/static/` contains
first-party CSS/JS authored for this project (covered by this project's own MIT license,
not third-party).

## 3. Analyzer-owned Node packages (source tracked at `wrapper/node_tools/`)

The tracked `wrapper/node_tools/package.json` + lockfile are always the install source.
The generated `node_modules/` is installed with `pnpm install --dir
<data-root>/runtime/<contract>/node_tools --frozen-lockfile --ignore-scripts` into that
analyzer-owned data-root runtime location — never a global binary, never resolved from
or installed into a target repository. (A legacy, pre-relocation install directly into
`wrapper/node_tools/node_modules` is honored as a fallback only when that location alone
is populated.)

| Package | Version pin | License | Role |
| --- | --- | --- | --- |
| [dependency-cruiser](https://github.com/sverweij/dependency-cruiser) | `==18.1.0` | MIT | JS/TS dependency graph and dependency-cycle detection. |
| [TypeScript](https://github.com/microsoft/TypeScript) (compiler API) | `==5.9.3` | Apache License 2.0 | `tsconfig` resolution and the JS/TS call graph. |

## 4. Go tool installed by the wrapper (analyzer-managed)

Installed on request into an analyzer-owned `GOBIN`
(`GOBIN=<data-root>/runtime/<contract>/go_tools/bin go install
golang.org/x/tools/cmd/callgraph@v0.48.0`) — never a global binary. (A legacy,
pre-relocation install into `wrapper/go_tools/bin` is honored as a fallback only when
the runtime location above has no binary.)

| Tool | Version pin | License | Role |
| --- | --- | --- | --- |
| [`callgraph`](https://pkg.go.dev/golang.org/x/tools/cmd/callgraph) (`golang.org/x/tools`) | `v0.48.0` | BSD 3-Clause "New" or "Revised" License | Builds the Go call graph (VTA analysis) for the Go lane. |

## 5. External tools invoked, not bundled or distributed

Project Analysis never installs or vendors these. Each is developer-managed: the
developer supplies it via their own package manager (Homebrew, pip, npm, go install, or
system packages), and the wrapper only invokes the binary already on `PATH`. Their
absence degrades the corresponding lane to disclosed reduced coverage; nothing about
these projects is redistributed here.

| Tool | License | Role |
| --- | --- | --- |
| [scc](https://github.com/boyter/scc) | Dual-licensed MIT / The Unlicense | Repository-wide size and language inventory. |
| [lizard](https://github.com/terryyin/lizard) | MIT | Complexity metrics (JS/TS/Go). |
| [jscpd](https://github.com/kucherenko/jscpd) | MIT | Within-repository and same-language cross-repository duplication detection. |
| [ast-grep](https://github.com/ast-grep/ast-grep) | MIT | Structural route, integration, table, and access-model discovery. |
| [staticcheck](https://github.com/dominikh/go-tools) | MIT | Go static-analysis quality lane. |
| [osv-scanner](https://github.com/google/osv-scanner) | Apache License 2.0 | Optional vulnerability evidence; network lane, off unless explicitly authorized for a run. |

## Notes

- Nothing in this file grants rights beyond each component's own license; this is a
  notice/inventory, not a sublicense.
- If a future version bundles or vendors an additional third-party asset, add it here
  and to `tools/manifest.json` in the same change.

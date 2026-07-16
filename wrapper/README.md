# Project Doctor wrapper

The wrapper executes the allowlisted Phase-0 toolchain from a discovery-produced
`TargetSpec`. It invokes tools, classifies their execution status, writes provenance
manifests, and produces sanitized bounded views. It does not interpret findings or
validate reports.

Create the project-local virtual environment first. The host Python is used only
to create the environment; all packages are installed into `wrapper/.venv`.

```bash
cd wrapper
python3 -m doctor_wrapper.bootstrap          # runtime + PyDriller
python3 -m doctor_wrapper.bootstrap --dev    # also install pytest

# One tool against one stable repository ID (the output path must be new)
.venv/bin/project-doctor-wrapper --targets targets.json --out output/run/signals \
  run --repo api-11112222 --tool scc

# All applicable local tools; add --include-network only with explicit approval
.venv/bin/project-doctor-wrapper --targets targets.json --out output/run/signals sweep

# Tests also use the isolated interpreter; shell activation is unnecessary
.venv/bin/python -m pytest
```

Set `--venv <path>` to keep the environment elsewhere. Re-running bootstrap is
safe and updates the same environment. `wrapper/.venv` is gitignored. Do not run
`pip install` with the host Python.

Network-capable definitions (`staticcheck`, `go list`, `osv-scanner`, and npm/yarn
outdated) are skipped unless `--include-network` is explicitly supplied, including
for the single-tool `run` command. Go tools may contact `GOPROXY` on a cold module
cache; the wrapper pins those requests to `proxy.golang.org` and
`sum.golang.org`, disables workspace/toolchain auto-dispatch, and never falls
back directly to dependency hosts. OSV sends dependency coordinates to
`api.osv.dev`; outdated checks send
package names and versions only to the fixed public npm/yarn registry. Target-owned
registry configuration and remote dependency URLs are refused rather than followed.
The orchestrator must obtain approval before this flag is used on private code.

The output directory must not already exist and must be outside every target repository.
Raw stdout/stderr stays under the self-gitignoring `signals/raw/` containment directory.
Only `*.view.txt`, manifests, and the run summary may be read by an agent. The normalized
manifest excludes volatile fields and supports byte-for-byte deterministic comparison.

PyDriller is primary for history analysis and bootstrap installs the pinned 2.10
release into the virtual environment. If the wrapper is deliberately run outside
that environment, set `PROJECT_DOCTOR_PYDRILLER_PYTHON` to an isolated Python
containing PyDriller 2.10; otherwise the lane uses the disclosed plain-Git fallback
and reports `partial`.

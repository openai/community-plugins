# Validation record

Qualification date: **2026-10-21**. This record covers the reusable v0.1 local
implementation. Customer policy approval, authenticated customer GitLab access,
second-human acceptance and external distribution remain separate pilot gates.
The final release receipt binds these results to source and package digests.

## Executed checks

| Check | Observed result | Scope |
| --- | --- | --- |
| Full suite, macOS arm64 / Python 3.14.6 | **221 passed**, no failures or skips, 40.53 seconds | Real pinned Helm and Kubeconform; all integration paths enabled |
| Minimum Python, macOS arm64 / Python 3.11.15 | **221 passed**, no failures or skips, 41.31 seconds | Separate environment installed from the unchanged public lockfile |
| Full suite, Linux arm64 / Python 3.14.8 | **221 passed**, no failures or skips, 15.06 seconds | Clean Docker image, no bind mounts, `--network none`; external network probe failed as expected |
| Source lint | Ruff passed | Runtime, scripts and tests |
| Independent package replay | Pass and expected risky-case failure; artifact hashes verified | Fresh environment, staged package path containing spaces, independent agent on the author host; not a second human or customer acceptance |
| 250-resource benchmark | Complete/pass, no findings or gaps, **8.86 seconds** | 125 Deployments and 125 Services, one replica change; full CLI/report write on a warm local host, excluding model time and provisioning |
| Local Codex installation | Installed and enabled as `devsecops@devsecops-local`, version 0.1.0 | Supported source-marketplace CLI flow; no public publication |
| Native behavior | 12 acceptance cases met expected behavior, with limits below | Installed skill/runtime, synthetic inputs, configured `gpt-6-astra` alias; two capped exploratory captures retained separately |
| Direct visual inspection | Desktop/mobile dark appearance and keyboard disclosure passed | Actual engine output with a severity override and an applied exception; light and print remain unverified |
| Authenticated customer GitLab | **Not tested** | Controlled HTTP contract tests do not establish customer authentication, network or GitLab-version compatibility |

The tested tools are **Helm 4.3.1**, **Kubeconform 0.8.0**, and 24 selected
**Kubernetes 1.34.0** schemas pinned in [`toolchain.lock.json`](../toolchain.lock.json).
The additional 1.36.5 set uses the same 24 kinds from upstream commit
`a6f9a32d2ccb64b6e4f5b41419b9c2e8ee0cce18`, with separate file hashes. Its proposal
tests run the actual pinned Helm and Kubeconform against both renderings, reject
1.34-only or modified schema bytes, and retain controlled publication status.
Set `DEVSECOPS_SCHEMAS_1365` to the explicitly provisioned schema root when it is
outside `~/.cache/devsecops/kubernetes-1.36.5/schemas`. These are local rendering
tests, not RKE2, Big Bang, admission or hosted GitLab acceptance.
The Linux image pins its base digest in [`tests/Dockerfile`](../tests/Dockerfile).
macOS amd64, Linux amd64 and Windows have not been qualified.

Native installation was exercised with Codex CLI 0.144.3. That older CLI could
not execute the configured `gpt-6-astra` model: the server required a newer host.
The existing desktop-bundled **0.162.0-alpha.17.2** runtime successfully started
the configured model without changing global CLI, model or authentication
settings. Failed startup attempts are retained separately and do not count as
passing skill evaluations. Unrelated pre-existing MCP startup warnings do not
represent integrations used by this plugin.

## Installed skill evaluation

Three repeated risky-change cases retained the introduced high privilege finding
and failed the configured gate. Three missing-schema cases preserved
`partial` / `unknown`, retained that same finding, and did not substitute schemas.
A benign natural-language case passed; ambiguous target selection asked for scope;
an unrelated writing request used no task tools. A deployment-boundary question
correctly explained the unsupported operation without running shell or deployment
commands. That last case attempted a TextEdit read, which was denied, and MCP
resource discovery; it is a boundary smoke test, not a zero-tool-use result.

The final refreshed installation matched all 65 packaged files. A separate
natural-language request supplied neither a skill name nor a plugin path. Codex
found the installed skill and ran its absolute entrypoint against a deliberately
dirty working tree. It produced the expected high finding and preserved all 11
working files, HEAD, index bytes/entries and dirty status. A saved-report follow-up
correctly explained the difference between a severity override and an exception,
including owner, reason, expiry and UTC evaluation date, without rerunning checks.

These are 12 acceptance cases on one configured model/client combination, not a
general accuracy benchmark. The initial natural-language case included runner
context, and one early missing-schema trace uses a relative entrypoint whose cwd
is not retained. The final unhinted discovery and later absolute-entrypoint runs
provide stronger installed-path evidence. Two exploratory missing-schema console
captures were capped; complete file-backed reruns supersede them for acceptance.
Earlier tests preceded the final skill instruction disabling Git optional index
refreshes; the final dirty-tree case specifically validated that change. The
model alias is recorded; an immutable server model snapshot was not exposed.

Canonical report schemas, artifact hashes and input receipts were independently
checked. Extra assistant-authored explanation/preservation files are distinguished
from the three artifacts covered by the engine's terminal manifest. Full traces
are retained locally; no customer data or authentication was used.

## Direct visual evidence

A real CLI review of the synthetic Helm fixture produced `complete` / `pass`
with two retained findings: one high finding excluded by an exact exception and
one high-to-medium severity override. The report explains both treatments at the
summary and finding level; the exception card displays owner, reason, scope,
expiry and the UTC evaluation date.

After explicit user authorization, the synthetic report was served from a
dedicated loopback-only directory. Chrome CUA inspected a 1628-pixel desktop
layout and a 390 × 844 viewport. At narrow width, `scrollWidth` and `clientWidth`
were both 390 pixels, including after keyboard expansion of evidence. Long paths
and hashes wrapped; cards were readable without observed overlap. The viewport
was reset, the tab closed and the temporary server stopped.

The local visual receipt retains report, renderer and screenshot hashes.
Screenshots and generated reports are excluded from the plugin package.
Light appearance and print rendering are not claimed as visually verified.

## Retained qualification evidence

The source checkout's ignored `work/` directory retains these local receipts:

- `mac-validation.json`, `mac-final-validation.log`, `mac-python311-validation.log`
- `linux-validation.json`, `linux-final-validation.log`, `linux-final-build.log`
- `clean-package-replay.json`, `benchmark.json`
- `validation/visual/qa-receipt.json` and desktop/mobile screenshots
- `native-evals/`: exact synthetic prompts, model traces, report artifacts,
  failure attempts and final assessments

The release ZIP is accompanied by a separate release receipt with exact source,
archive, installed-content and artifact identities. These transient receipts do
not ship as runtime components. Full model traces remain local because host
startup can expose unrelated configuration metadata; only sanitized qualification
summaries accompany the distributable.

## Test map

| Test file | Main purpose |
| --- | --- |
| `test_resources.py` | Strict parsing, bounds, resource identity/scope, CRDs and ambiguity |
| `test_checks.py` | Value-free rule evidence, classification, selectors, RBAC, network and lifecycle semantics |
| `test_boundaries.py` | Snapshot trust, symlinks/archives, offline schema boundaries, tool isolation and failure outcomes |
| `test_policy.py` | Profile/gate validation, baseline trust, severity overrides and scoped expiring exceptions |
| `test_prerequisites.py` | Missing tools and independent target coverage; public dependency lock and schema behavior |
| `test_e2e.py` | Real Helm/Kubeconform pass/fail/unknown, redaction, checkout preservation and report presentation |
| `test_gitlab.py` | GET-only bounded collection, identities, races, pagination, redirects, token safety and artifact binding |
| `test_gitlab_e2e.py` | Controlled GitLab evidence combined with exact local Git revisions and actual render/validation tools |

The fixture server's loopback injection is a test-only seam, not a production
CLI option. No test result claims live cluster admission or deployed behavior.

## Reproduce

```sh
uv sync --frozen
DEVSECOPS_HELM=/absolute/approved/tools/helm \
DEVSECOPS_KUBECONFORM=/absolute/approved/tools/kubeconform \
DEVSECOPS_SCHEMAS=/absolute/approved/local-schemas \
uv run --frozen pytest -q
```

Without the three tool/schema paths, real-tool cases explicitly skip. A passing
unit-only invocation is not equivalent to the full results above. Capture tool
versions/hashes and schema provenance through `devsecops-review doctor` and the
review artifacts. Requalify changed runtime or skill behavior before distributing
a later release; documentation-only changes are tracked separately.

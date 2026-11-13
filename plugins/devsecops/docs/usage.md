# Usage

## Prepare the local runtime

The CLI requires Python 3.11+, Git, and the project's locked Python dependencies.
Helm targets also require Helm. Complete schema validation requires Kubeconform
and local schemas for the declared Kubernetes version.

From a writable source checkout (not the installed plugin cache):

```sh
uv sync --frozen
uv run --frozen devsecops-review --help
uv run --frozen devsecops-review review --help
```

Use your organization's approved tool distributions. Initial dependency/tool
setup may use the network. Do not confuse local rendering with offline model
inference or an approved customer deployment surface.

For an approved evaluation environment, an explicit bootstrap helper provisions
the release's pinned tools and selected schemas:

```sh
python3 scripts/bootstrap.py --cache /absolute/approved/tool-cache
```

The [toolchain lock](../toolchain.lock.json) pins download hashes, Helm 4.3.1,
Kubeconform 0.8.0, and a specific schema-source revision. The helper supports macOS
and Linux on arm64/amd64, verifies downloads, and prints the resulting tool and
schema paths. It does not install tools globally. The default schema selection
remains Kubernetes 1.34.0. To provision the additional pinned 1.36.5 set explicitly:

```sh
python3 scripts/bootstrap.py --cache /absolute/approved/tool-cache --kubernetes-version 1.36.5
```

Each set covers the same 24 common resource kinds; neither is the full API or
custom-resource catalog. Provisioning another selected version preserves cached
schema files for the previous version. Use independently approved local schemas
for other versions and kinds. The review itself does not invoke this helper.

Supply approved binaries using `--helm /absolute/path/helm` and
`--kubeconform /absolute/path/kubeconform`. The local schema directory contains
Kubeconform's versioned strict layout, for example:

```text
schemas/
  v1.34.0-standalone-strict/
    deployment-apps-v1.json
    service-v1.json
    serviceaccount-v1.json
    role-rbac-v1.json
    rolebinding-rbac-v1.json
    networkpolicy-networking-v1.json
    persistentvolumeclaim-v1.json
    secret-v1.json
```

Schema filenames must match the actual validator and schema distribution. A
missing kind/version schema is incomplete validation, including for custom
resources. The review must not retrieve schemas from the internet as a fallback.

## Install in Codex

Build the local marketplace with `scripts/build_plugin.py --marketplace-dir`
using a new destination, then run `codex plugin marketplace add DESTINATION`
and `codex plugin add devsecops@devsecops-local`. Use `codex plugin list --json`
to confirm it is installed and enabled. Start a new session for discovery.
These commands were exercised on the versions in the support matrix; other
Codex surfaces may have a different installation flow.

The installed plugin root is reported by the installer. Dependencies are not
bundled as executable environments. To keep a locked environment outside the
managed plugin cache, prepare it explicitly from that root:

```sh
UV_PROJECT_ENVIRONMENT=/absolute/approved/devsecops-env \
  uv sync --project /absolute/installed/plugin --frozen --no-dev --no-install-project
/absolute/approved/devsecops-env/bin/python -B \
  /absolute/installed/plugin/scripts/devsecops_review.py doctor
```

Supply that interpreter to Codex with the installed plugin root. It can run the
installed `scripts/devsecops_review.py` directly; the entry point loads its
adjacent packaged source. This is also suitable for a preprovisioned runner.
Avoid editing the managed plugin cache. Keep the selected environment paired
with the release's `uv.lock` and revalidate after upgrades. `--no-install-project`
installs only the locked dependencies; the wrapper loads the immutable packaged
source directly. `-B` prevents Python bytecode writes inside the plugin cache.
Use the organization's approved Python index or mirror during setup. This
package's lock names public PyPI; pass `--default-index https://pypi.org/simple`
when that is your selected source and an ambient index would override it. Add
`--offline` for an already populated dependency cache. Do not run `uv sync` or
`uv run` against the installed cache with its default environment location.

If a host rejects the configured model as requiring a newer Codex version, use a
compatible approved app/CLI. That is a host compatibility failure, not a review
result; do not mark the unexecuted review as passed. No global CLI or model
configuration change is required by this plugin.

For an upgrade, preserve the prior source snapshot and ZIP, replace the plugin
in its existing registered source marketplace, then install the new version
through the supported host flow. A marketplace name already registered from a
different path cannot simply be added again. Keep unrelated marketplace entries
intact. Confirm the installed version and run the synthetic demo. To roll back,
restore the previous source marketplace/version through the same flow and
reproduce the demo. Never overwrite an earlier report to make it look current.

## Select the repository and revisions

Use the exact baseline commit or a local ref and an exact candidate revision.
`WORKTREE` reviews local files while preserving the checkout and index. There is
no automatic fetch or branch switch. A missing commit must be made available
through your normal authorized Git workflow before review.

Choose an output directory that does not already exist. Keep artifacts outside
the entire reviewed repository; the CLI rejects output anywhere inside that
checkout, including paths outside the selected targets.
Use a canonical path without symlink components; on macOS `/private/tmp/...`
is preferable to the `/tmp/...` alias for an output directory.

```sh
uv run --frozen devsecops-review review \
  --repo /absolute/repository \
  --base BASE_COMMIT \
  --head WORKTREE \
  --profile /absolute/review-profile.json \
  --target staging \
  --helm /absolute/tools/helm \
  --kubeconform /absolute/tools/kubeconform \
  --schema-location /absolute/local-schemas \
  --output /absolute/reviews/new-review
```

The direct entry point is also available when the dependencies are already
installed in the selected Python environment:

```sh
python scripts/devsecops_review.py review --help
```

## Define a profile

Profiles are JSON data, not executable configuration. Copy an example and review
its paths and assumptions with the platform owner. This minimal Helm profile is
illustrative:

```json
{
  "schema_version": 1,
  "id": "platform-staging",
  "revision": "1",
  "targets": [
    {
      "name": "staging",
      "type": "helm",
      "path": "charts/web",
      "values": ["values/staging.yaml"],
      "namespace": "web-staging",
      "release": "web",
      "kubernetes_version": "1.34.0",
      "api_versions": [],
      "mode": "upgrade"
    }
  ],
  "policy": {
    "require_image_digest": true,
    "allowed_registries": ["registry.example.invalid"],
    "required_labels": ["team"]
  }
}
```

- `path` and every `values` entry are relative to the repository root.
- Both revisions use the same declared render context. For a profile change,
  make the intended context explicit and retain the previous review for comparison.
- `api_versions` models declared API capabilities; it is not cluster discovery.
- `mode` selects install/upgrade rendering assumptions, not a deployment action.
- For YAML targets, use `type: "manifests"`, a file or directory `path`, and
  `values: []`. Keep the target Kubernetes version and namespace explicit.
- Repeat `--target staging --target supporting-manifests` for multiple named
  targets. Do not infer that reviewing one target covers the repository.

The engine records the profile ID and SHA-256. Keep real customer profiles and
schemas under the customer's normal access and change-control practices. The
[Lockheed example](../profiles/lockheed.example.json) is an unapproved example;
it contains no claim about actual Lockheed topology, access, or security policy.

A profile outside the reviewed repository is read as explicit trusted
configuration. A profile inside that repository is read from its regular tracked
file at the resolved **baseline commit**, even if the candidate edits, removes,
or replaces it. Candidate changes cannot relax the policy that reviews them.
To introduce a policy change intentionally, review it separately and select an
approved external profile or a baseline containing the approved version.

See [policy configuration](policy.md) for severity gates and scoped exceptions.
The machine-readable [profile schema](../schemas/profile.schema.json) describes
the versioned input contract; semantic validation also checks known rules,
exact identities, canonical expiry dates and supported input bounds.

## Add GitLab context

The optional reader uses an explicitly identified HTTPS GitLab instance, project,
and MR IID. Provision a suitable read-only token through your approved secret
mechanism and supply only its environment-variable name to the CLI:

```sh
uv run --frozen devsecops-review review \
  --repo /absolute/repository --base BASE_COMMIT --head CANDIDATE_COMMIT \
  --profile /absolute/review-profile.json --target staging \
  --schema-location /absolute/local-schemas \
  --output /absolute/reviews/new-review \
  --gitlab-url https://gitlab.example.invalid \
  --gitlab-project platform/web \
  --gitlab-mr 123 \
  --gitlab-token-env GITLAB_TOKEN \
  --artifact 456:reports/kubernetes-review.json
```

The URL and project above are placeholders. Do not put token values in commands,
profiles, reports, or chat. Artifact flags identify an exact job and path and can
be repeated when supported by the CLI. MR/source mismatches, inaccessible or
expired artifacts, and incomplete reads must remain visible. The reader does not
post comments, approve or merge MRs, trigger pipelines, or execute artifacts.

## Interpret and share the result

Open `report.html` in a browser for the review. The HTML is self-contained, works
without JavaScript, supports light/dark appearance and narrow screens, and escapes
all report content. Use `report.md` for a text review and `report.json` for tooling.

The decision and completion status are separate. A reported finding is not
automatically a new regression: use the finding's change classification and cited
resource. A missing schema, tool, dependency, or source revision is a gap, not a
clean result. See [Limitations](limitations.md).

The [report schema](../schemas/report.schema.json) is validated before writing
artifacts. Reports include run identity and start/finish timestamps; the terminal
manifest contains final report sizes and SHA-256 hashes. Early failures produce
an `error` / `unknown` report when a new safe output directory can be created.
An existing or unsafe output path is never overwritten merely to emit a failure
report. A missing failure artifact therefore requires reading the CLI exit and
sanitized error, not inferring that the review ran.

Review artifacts can still reveal internal resource names, repository paths,
policy assumptions, and infrastructure metadata. Share them through the same
approved channels as the source change. The engine's redaction is not a general
data-loss-prevention system.

## Run the integration suite

Supply real approved tools and local schemas for the synthetic Kubernetes 1.34.0
fixtures:

```sh
DEVSECOPS_HELM=/absolute/tools/helm \
DEVSECOPS_KUBECONFORM=/absolute/tools/kubeconform \
DEVSECOPS_SCHEMAS=/absolute/local-schemas \
uv run --frozen pytest
```

The tests create temporary Git repositories, exercise real rendering/validation,
check risk findings and incomplete evidence, verify Secret redaction, and ensure
the source checkout is preserved. No test requires cluster access.

## Install from a local source marketplace

Current Codex CLI hosts support a local marketplace root containing
`.agents/plugins/marketplace.json`. Source paths in that manifest are relative to
the marketplace root. The package helper can create a clean source snapshot:

```sh
uv run --frozen python scripts/build_plugin.py --marketplace-dir work/marketplace
```

This stages the plugin at `work/marketplace/plugins/devsecops` and marks it
available in the `devsecops-local` marketplace. Staging does not install it or
change Codex configuration. After deciding to install that reviewed snapshot:

```sh
codex plugin marketplace add /absolute/plugin-source/work/marketplace --json
codex plugin add devsecops@devsecops-local --json
codex plugin list --json
```

Confirm the returned component diagnostics and that the expected skill appears in
the target host. Run a synthetic review through the installed skill to establish
behavior; a successful add command alone is insufficient. The installed package
needs its locked Python environment and approved CLI tools in that host.

Edit source files, then build a fresh staging directory for updates. Do not edit
the installed version cache. Use the host's supported marketplace refresh and
plugin reinstall flow, checking `--help` because commands can differ across
Codex versions. Keep the prior reviewed package for rollback.

# DevSecOps

**Kubernetes and GitOps change review for Codex.**

Review an exact Git change with real Helm, Kustomize and Kubernetes schema tooling.
Get concrete findings, a readable HTML report, and explicit gaps when required
evidence is missing. Optional read-only GitLab and runtime evidence connects
code review to delivery and recovery.

This is the **alpha.14 community contribution preview**. All five workflows and
the factory runtime are included; the live factory demo and narrated recording
remain open. [Preview status and package identity](docs/community-preview.md)
distinguish this copy from the original R20 candidate.

## Start a review

Requirements: Python 3.11+, `uv`, Git, Helm for Helm targets, Kubeconform, and a
local schema directory for the target Kubernetes version. Set up approved tools
and dependencies before the review; see [Usage](docs/usage.md).

An explicit provisioning helper downloads checksum-pinned Helm, Kubeconform and
24 selected Kubernetes 1.34.0 schemas on macOS/Linux arm64 or amd64:

```sh
python3 scripts/bootstrap.py --cache /absolute/approved/tool-cache
```

Review [the toolchain lock](toolchain.lock.json) and your environment's software
policy before provisioning. Use the tool/schema paths printed by the helper in
the review command. Provisioning and Python dependency setup are separate from
the offline local review.

From the plugin source directory:

```sh
uv sync --frozen
uv run --frozen devsecops-review review \
  --repo /absolute/path/to/repository \
  --base BASE_COMMIT \
  --head WORKTREE \
  --profile /absolute/path/to/review-profile.json \
  --target web \
  --schema-location /absolute/path/to/local-schemas \
  --output /absolute/path/to/new-review-directory
```

Copy and adapt [the generic example](profiles/generic.example.json) to your
repository. Paths in the profile are relative to that repository. Each review
uses a new output directory and leaves the source checkout and Git index intact.

In Codex, select the installed DevSecOps plugin and ask:

> Use $review-kubernetes-change to review my working-tree change against commit
> BASE_COMMIT. Use `/absolute/profile.json`, target `web`, and the approved
> local schema directory. Explain new findings and incomplete checks.

## Try the synthetic example

After provisioning the pinned tools in the default cache, produce a complete
review without supplying a repository or customer data:

```sh
uv run --frozen python scripts/demo.py --output /absolute/new-demo
uv run --frozen python scripts/demo.py --risk --output /absolute/new-risk-demo
```

Use `--helm`, `--kubeconform` and `--schema-location` with the demo if your
approved tool cache is elsewhere. The first case passes. The second intentionally
exits `1` with actionable findings. Open `report.html` in the selected output directory. The demo creates
its own temporary Git history and leaves this source checkout unchanged.

## Five workflows

| Skill | Use it for | Current scope |
| --- | --- | --- |
| [`$review-kubernetes-change`](skills/review-kubernetes-change/SKILL.md) | Review manifests, Helm or Kustomize changes and optional GitLab CI evidence | Implemented local review; unavailable validation stays unknown |
| [`$diagnose-gitops-deployment`](skills/diagnose-gitops-deployment/SKILL.md) | Compare baseline and fault evidence from Argo CD, Kubernetes and Prometheus | Implemented scoped, read-only evidence collection and diagnosis |
| [`$prepare-gitops-correction`](skills/prepare-gitops-correction/SKILL.md) | Prepare and locally validate a correction | Implemented review and exact rendered-resource export; no implicit merge or deployment |
| [`$verify-gitops-recovery`](skills/verify-gitops-recovery/SKILL.md) | Check whether observations prove functional recovery | Implemented evidence checks; missing provenance or functional proof stays unknown |
| [`$run-software-factory-demo`](skills/run-software-factory-demo/SKILL.md) | Operate the configured build, delivery and recovery lab | Preview runtime; live end-to-end qualification and recording remain open |

Use the [generic profile](profiles/generic.example.json) as a starting point.
The customer-named profile is an unconfigured illustration, not approved customer
policy. Profile configuration and a passing review do not certify compliance.

For the standard **code → GitLab CI → cloud → Argo CD → Prometheus** flow, select
[portable-gitops-v1](docs/portable-gitops.md). The installed diagnosis/recovery
skills collect and compare actual scoped API evidence with explicit version and
freshness checks. Controller health, workload readiness and functional telemetry
remain separate conditions; this profile retains aggregate unknown until the
missing provenance is independently established. Its collector has no deployment
operations. The [private Linux stack runbook](demo/portable-gitops/README.md)
provides the explicit real-service setup, CI, fault and correction sequence.
Its trusted runner is for one authorized disposable VM; it is not a production
runner design. The original Iron Bank factory remains a separate path below.

## Read the result

| Artifact | Purpose |
| --- | --- |
| `report.html` | Standalone responsive review with findings, evidence, validation and coverage |
| `report.md` | Paste-friendly engineering summary |
| `report.json` | Authoritative machine-readable review |
| `run-manifest.json` | Terminal artifact hashes and run metadata |

| Decision | Meaning | CLI exit |
| --- | --- | --- |
| `pass` | The required checks completed without a blocking finding | `0` |
| `fail` | Completed review found a blocking condition | `1` |
| `unknown` | Required evidence or validation is incomplete, or the run failed | `2` |

A local pass does not prove that Kubernetes will admit the change, that a CNI
enforces a network policy, or that the deployed workload is healthy. Read
[limitations and coverage](docs/limitations.md) before using the result as release
evidence.

## Factory demo preview

The intended visible flow is **code change → GitLab CI → verified image → cloud
Kubernetes → Argo CD → Prometheus → correction and recovery**. The included
[demo runbook](docs/demo/ironbank-e2e.md) defines the actual scenario and evidence;
a script, test fixture or healthy control plane is not a completed live demo.

The runtime includes [Registry1 and Cosign verification](docs/interfaces/image-verification.md),
[local Syft/Grype scanning and rootless builds](docs/interfaces/build-chain.md),
[application signing](docs/interfaces/mission-signing.md), and
[immutable evidence publication](docs/interfaces/mission-publication.md).
The [owned Linux backend](docs/interfaces/owned-linux-backend.md) composes explicit
lab operations, service and CI wiring, native review handoffs, recovery observations
and owned-resource cleanup. Operator credentials, resource scope and approvals
are required separately; local review remains available without a cloud lab.

See the [implementation ledger](docs/demo/implementation-status.md) and
[community preview status](docs/community-preview.md) before using the factory
workflow. This package does not include completed live-demo evidence or an MP4.

## Distribution

The package contains a portable `plugin.json`, a synchronized Codex compatibility
manifest, five native skills, the Python runtime, documentation and synthetic
fixtures. It has no required cloud service or MCP process. Initial setup can
require network access to acquire tools and dependencies; review inputs and
schemas are local unless GitLab context is explicitly requested.

Original source is licensed under [Apache-2.0](LICENSE); see [NOTICE](NOTICE)
and the [dependency inventory](docs/dependencies.md) for third-party boundaries.

Build a ZIP outside the source directory:

```sh
uv run --frozen python scripts/build_plugin.py \
  --output /absolute/path/outside-the-source/devsecops-0.2.0-alpha.14.zip
```

The builder validates component metadata and excludes local environments, tool
binaries, caches, generated reports and non-example profiles. It includes
the same `SHA256SUMS` content manifest in both the ZIP and the staged installation
source. The builder's `package_sha256` identifies the exact manifest bytes;
`sha256` identifies the ZIP file. Checksums are integrity evidence, not a publisher
signature or proof that Codex loaded that installation.

For a Codex CLI with local marketplace support, stage and install the source:

```sh
uv run --frozen python scripts/build_plugin.py \
  --marketplace-dir /absolute/new-marketplace
codex plugin marketplace add /absolute/new-marketplace
codex plugin add devsecops@devsecops-local
```

Start a new Codex session and invoke `$review-kubernetes-change`. Set up the
locked dependencies for the installed runtime, or provide an approved Python
interpreter with those dependencies; see [installation](docs/usage.md#install-in-codex).
The [support matrix](docs/support-matrix.md) identifies the tested host versions.
Packaging, local installation, workspace sharing and public publication are
separate actions. This package has not been publicly published or approved for
customer deployment.

## Development and validation

```sh
uv sync --frozen
uv run --frozen pytest
```

Real-tool integration cases use `DEVSECOPS_HELM`, `DEVSECOPS_KUBECONFORM`, and
`DEVSECOPS_SCHEMAS`. Without these, those tests explicitly skip rather than
claiming the integration works. The [fixtures](tests/fixtures/README.md) contain
synthetic good and deliberately risky changes and never require a cluster.

See [Usage](docs/usage.md) for profile fields, CI evidence, and test commands.
The [policy guide](docs/policy.md), [profile schema](schemas/profile.schema.json),
and [report schema](schemas/report.schema.json) define the versioned contracts.
The [release notes](docs/release-notes.md), [dependency inventory](docs/dependencies.md),
[support matrix](docs/support-matrix.md), [rule catalog](docs/rules.md),
[validation record](docs/test-validation.md), and
[release checklist](docs/release-checklist.md) separate current coverage from
the evidence and remaining customer-pilot qualification.

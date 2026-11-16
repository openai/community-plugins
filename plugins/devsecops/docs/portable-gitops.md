# Portable GitOps observations

Select **`portable-gitops-v1`** for an ordinary GitLab → container build →
Kubernetes → Argo CD → Prometheus demonstration. This is a separate, read-only
observation path in the installed plugin. It does not replace the Iron Bank
factory, its contracts, image policy or delivery gates.

The plugin reads the selected Application, named Deployment and Service, owned
workload resources, and four fixed Prometheus queries. It reports controller
sync/health, workload readiness, fresh scrape results and functional counter
progress separately. A healthy controller can coexist with failing telemetry.
The aggregate decision remains **unknown**: this path does not authenticate the
build, image publisher, delivered patch, environment or recovery provenance.

## Configure the actual services

Use the approved dependency interpreter and resolve the installed plugin root;
do not modify its cache. Copy the
[configuration template](examples/portable-runtime.example.json) to a private
operator directory and replace its endpoints, resource names, exact expected Git
commit and selectors. The
[closed configuration schema](../schemas/portable-runtime-config.schema.json)
rejects inline credentials and extra fields. Keep credentials in the named
environment variables; a CA bundle is a local absolute path. Omit `token_env` for
an intentionally unauthenticated service on an approved private network.

Every origin must be HTTPS with verified certificates, no user information, query
or path prefix. Use a trusted private TLS ingress or an explicitly configured
TLS endpoint reached through an authorized tunnel. Do not use insecure TLS,
cleartext HTTP or an automatically inferred proxy. The collector does not create
accounts, tunnels, credentials or infrastructure. Kubernetes credentials should
permit only scoped reads of Deployments, ReplicaSets, Pods, Services,
EndpointSlices, NetworkPolicies and Events. Argo CD needs Application reads;
Prometheus needs query access. RBAC denials remain visible evidence gaps.

Compatibility is deliberately explicit:

| Provider | Supported read contract |
| --- | --- |
| Argo CD | `3.5.6` Application API; other observed versions are retained with an unsupported-version gap. One Git source with a full 40- or 64-character commit ID. No chart/plugin/hydration resolver. |
| Kubernetes | `apps/v1`, core `v1`, `discovery.k8s.io/v1`, `networking.k8s.io/v1`; scoped named resources and bounded lists. This is API-shape support, not blanket distribution qualification. |
| Prometheus | `/api/v1/query_range` matrix API for `up`, `timestamp(up)`, `mission_events_total`, `timestamp(mission_events_total)`. Exact `namespace`, `service`, `job`, `instance` labels are required. No arbitrary PromQL. |

The default observation window is 60 seconds at 15-second steps, requiring at
least three distinct source samples no more than 45 seconds old. All selected
limits are saved in the scope and must match across compared phases. A stable
Service target can keep `instance` consistent across Pod replacements. Scrape
evaluation timestamps are not substituted for actual metric source timestamps.

## Collect baseline, fault and recovery

The user must authorize these named service reads. Saved evidence alone does
not authorize collection. Run the installed entry point:

```sh
"$DEVSECOPS_PYTHON" -B "$DEVSECOPS_PLUGIN_ROOT/scripts/observe_gitops.py" collect \
  --config /absolute/private/baseline-config.json \
  --phase baseline --output /absolute/private/new-baseline
```

After the separately authorized fault delivery, make a new operator config with
the **actual selected fault commit** and collect `--phase fault` into a new
directory. Keep the same service identities and selectors. Do not reuse a
healthy snapshot or inject sample data. When useful, `--not-before` also applies
to baseline/fault collection to reject earlier metric samples.

After the separately authorized correction is built and delivered through the
real pipeline, record its actual commit and delivery time. Allow a complete
observation window plus a scrape interval to elapse. Then collect:

```sh
"$DEVSECOPS_PYTHON" -B "$DEVSECOPS_PLUGIN_ROOT/scripts/observe_gitops.py" collect \
  --config /absolute/private/recovered-config.json \
  --phase recovered --not-before 2026-11-12T20:00:00Z \
  --output /absolute/private/new-recovered
```

The timestamp above is illustrative; supply the actual operator-recorded time.
It is a declaration, not authenticated delivery proof. Recovered collection
requires it, rejects source samples at or before it, and leaves controller
conditions unknown if reconciliation predates it. Do not change timestamps or
weaken the freshness rules to force a result. Each invocation reads the current
services and returns without a background watcher or an implicit retry.

## Diagnose and compare offline

Use the existing **diagnose-gitops-deployment** skill for the explicit portable
profile. It can compare the actual baseline and fault evidence and explain which
conditions changed while distinguishing hypotheses from demonstrated causes:

```sh
"$DEVSECOPS_PYTHON" -B "$DEVSECOPS_PLUGIN_ROOT/scripts/observe_gitops.py" compare \
  --baseline /absolute/private/new-baseline/portable-observation.json \
  --fault /absolute/private/new-fault/portable-observation.json \
  --output /absolute/private/new-diagnosis
```

Use **prepare-gitops-correction** and **review-kubernetes-change** to prepare and
validate the actual code/config correction; deployment uses its separately
authorized workflow. The portable observation command has no mutation methods.

The **verify-gitops-recovery** skill routes explicitly selected portable inputs
to the same offline comparison with an added `--recovered` path:

```sh
"$DEVSECOPS_PYTHON" -B "$DEVSECOPS_PLUGIN_ROOT/scripts/observe_gitops.py" compare \
  --baseline /absolute/private/new-baseline/portable-observation.json \
  --fault /absolute/private/new-fault/portable-observation.json \
  --recovered /absolute/private/new-recovered/portable-observation.json \
  --output /absolute/private/new-comparison
```

Comparison checks each terminal manifest and report hash, the profile and phase,
consistent service scope, and collection chronology. `recovery_observed` means a
specific recorded condition changed from fail to pass; the declared recovered
delivery must follow the completed fault observation. It does **not** mean the
whole deployment or factory is qualified. Comparison performs no network reads
and does not infer authority from paths embedded in evidence.

Every successful invocation writes private `0700` directories and `0600` files:
the observation/comparison JSON, `report.md`, `report.html`, and terminal
`portable-manifest.json`. Inspect the JSON's provider results, sanitized HTTP
receipts, exact source samples and required gaps. The terminal manifest means the
command finished writing; it does not mean a security/recovery gate passed.
Exit **2** means completed evidence with aggregate unknown. Exit **3** means the
command could not complete. Existing output directories and symlinks are rejected.

The Python test hooks label their reports `controlled-test`. `verified-https`
describes transport to the configured endpoints; it is not an attestation that
the endpoints are a real production system. Local hashes establish integrity,
not origin authentication. Single Kubernetes snapshots do not prove stable
images throughout a metric window. HTTP reachability, negative network controls
and policy causality remain outside this portable comparison. Keep those
limitations visible in the narrated demonstration and the public contribution.

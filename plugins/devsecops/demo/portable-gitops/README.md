# Private GitLab → Kubernetes → Argo CD → Prometheus demonstration

This is an operator-run scenario for **one newly authorized, disposable Ubuntu
AMD64 VM** (16 CPUs/64 GiB recommended). It creates real services and a real CI
pipeline. It is not a production installation or an enterprise factory qualification.
A publish retry compares the complete chart path set and file bytes at one
immutable deployment revision. Extra or missing templates require an explicit
reviewed chart update; they are never silently retained from another source.
The original Iron Bank, Big Bang, publisher, and hosted acceptance contracts remain
separate. The portable observer deliberately leaves aggregate assurance `unknown`.

Do not run these scripts on an existing shared Devbox. Cloud creation, cost limits,
expiry and final cloud deletion belong to the operator. Nothing here creates cloud
resources or changes an existing machine implicitly. The trusted shell runner has
Docker access on this disposable VM; it must accept only this synthetic private
project, never untrusted merge requests or customer code.
The `configure` stage disables public signup and Web IDE single-origin fallback,
then reloads and verifies both instance settings before creating its API token.
The runner shares the operator UID and Docker is root-equivalent: it can access
local cluster/admin credentials and the demo CA key. This is a trusted disposable
demo boundary, **not credential isolation or a production runner design**.

## Selected services and network

- GitLab CE19.5.5 and its bundled registry; dedicated GitLab Runner19.5.1.
- Native k3s v1.34.13+k3s1, with default Flannel **and its embedded NetworkPolicy
  controller enabled**. No nested VM, QEMU, KVM, or disabled policy controller.
- Argo CD3.5.6, matching the plugin's supported read API; Prometheus3.13.5.
- Existing `software-factory/apps/mission-status` and `charts/mission-status`.
- Namespace `mission-demo`, Helm release/Deployment/Service `mission-api`, Argo
  application `mission-demo` in project `demo`.
- Prometheus target `mission-api.mission-demo.svc:9090`; labels `job=mission-api`,
  `namespace=mission-demo`, `service=mission-api` and that exact `instance`.

GitLab8443 and registry5050 bind only the VM's supplied privateIP. Argo8444 and
Prometheus8445 bind loopback through recorded kubectl-forward units. Native
Kubernetes6443 remains behind the approved private VM network rules. There is no
public workload ingress. Use the already approved management connection to forward
these exact ports. Each HTTPS service has a verified CA; **do not use `-k`, disable
TLS verification, or expose the Docker socket over TCP**. GitLab's canonical links
use its privateIP; local-forward browser navigation can require deliberate origin
mapping by the operator. The scripts do not alter the workstation's hosts file.
The generated CA includes the certificate-signing key usage required by strict
X.509 clients. Readiness checks verify the external HTTPS sign-in page and the
exact owned GitLab container's localhost `/-/readiness?all=1` dependency checks,
alongside Kubernetes `/readyz` in the same attempt. GitLab's monitoring-IP
whitelist stays unchanged; an external monitoring404 is not a readiness signal.

## Inputs and prerequisites

The VM must already have `docker` with its Buildx plugin, `git`, `python3`, `openssl`, `curl`, `awk`,
`sha256sum`, `install`, systemd and `skopeo`. Package installation is a separate
approved operator action on the disposable VM. The user must be able to run Docker
and sudo the specific transient services/registry-CA installation used here.
This demo requires Docker's **classic `overlay2` image store**. Fresh Docker29
installations default to the containerd store, where an image ID can identify a
manifest or index instead of the config digest used by this demo. The `prepare`,
`check`, `services` and `configure` stages reject an incompatible store before
their work. On a separately approved, empty disposable Docker host, preserve any
existing daemon settings and set `features.containerd-snapshotter` to `false`
and `storage-driver` to `overlay2` in `/etc/docker/daemon.json`; validate the
configuration, restart Docker, then verify `docker info --format '{{.Driver}}'`
returns `overlay2`. Do not switch a daemon with existing workloads. Switching
stores hides the other store's images without deleting them; re-acquire the
selected images afterward. See the official [storage documentation](https://docs.docker.com/engine/storage/containerd/)
and [daemon configuration reference](https://docs.docker.com/reference/cli/dockerd/).
The base importer copies the bounded OCI layout into a private directory as the
current user, verifies content-addressed blob hashes, and does not apply archive
ownership or permissions. It uses Skopeo only for local OCI-to-Docker-archive conversion,
then native `docker image load`; it verifies the original config digest both
before and after loading. This avoids Ubuntu Skopeo's older daemon API without
lowering Docker's minimum API version. Conversion archives and receipts remain
private for diagnosis.
The VM needs HTTPS egress to official GitHub release assets, GitLab Runner's
official distribution bucket, Docker Hub and Argo's documented image registries.
These scripts do not pass cloud credentials to jobs. Keep cloud credentials and
managed cloud identities off this VM; Docker access is not an isolation boundary.

## Shell runner account preflight

GitLab's shell executor loads the runner account's login and logout profiles.
Before `configure`, run this check as the same account that will run the trusted
shell runner. It loads those profiles but does not execute the application image:

```bash
env -u SHLVL /bin/bash --login <<'PROBE'
set -eo pipefail
printf 'headless-runner-profile-probe\n'
exit 0
PROBE
```

Require exit status 0. Ubuntu's stock `.bash_logout` can instead return 1 because
it calls `clear_console` without a terminal. If the actual file contains the
confirmed stock block, preserve the original privately and change only its
condition to:

```bash
if [ "$SHLVL" = 1 ] && [ -t 0 ]; then
    [ -x /usr/bin/clear_console ] && /usr/bin/clear_console -q
fi
```

This retains console clearing when a terminal exists. Repeat the same headless
check and require 0 before starting CI. Do not suppress arbitrary profile errors,
replace a custom profile, or apply this change on an existing shared machine.
The plugin does not silently edit account profiles. No runner restart is needed
for this profile correction; each job loads the account's current profile.

See GitLab's [shell profile guidance](https://docs.gitlab.com/runner/shells/#shell-profile-loading).

## Prepare the retained inputs

Use `bundle_inputs.py --help` to assemble a new private input directory on the
operator machine from seven explicitly selected, already retained files:

1. The selected LinuxAMD64 Python base OCI archive.
2. Pinned Linux Syft and Grype executables from the existing supply-chain lock.
3. `prometheus_client-0.26.0-py3-none-any.whl` (the existing hash-locked app dependency).
4. Genuine Grype `database.tar.zst` and its original `latest.json`.
5. The exact scan-policy JSON: an empty High-selector list for no exception, or
   explicitly approved complete High selectors with an expiry.

The input manifest binds each file's hash/size. It is not proof of publisher
authenticity or an exception approval. Keep the original acquisition/approval
receipts separately. Syft/Grype also must match the packaged LinuxAMD64
`supplychain.lock.json` before execution. The exception input is:

```json
{
  "base_manifest_digest": "sha256:<actual selected OCI manifest>",
  "expires_at": "<explicit approved UTC expiry>",
  "allowed_high_selectors": []
}
```

An empty list permits no High findings and records `passed` on a matching scan.
A temporary exception must list every complete approved selector, with exactly
`vulnerability`, `namespace`, `severity` (`High`), `type`, `name`, `version`, and
`purl`. Up to256 unique selectors are supported; the observed set must match
exactly, including occurrence count. Successful nonempty policies are labeled
`temporary-exception`. A changed or missing selector blocks publication; Critical
and unknown severities always block. The historical filename `exception.json`
remains for input compatibility. An optional legacy `allowed_high_findings` list
must be the exact three-field projection of the complete selectors.

The input file does not itself grant an exception: retain the actual approval
separately. Base-manifest, expiry and policy-byte hashes remain bound from scan
through publication. The original120-hour database freshness bound stays in force.
No automatic update, new database acquisition, or stale-database bypass occurs.
The same retained base config digest is checked before the actual offline build.

## Run the explicit stages

Transfer the public plugin and private input bundle through the approved management
connection. Set `STATE` to a new absolute private directory **outside the plugin**,
`INPUTS` to the transferred bundle, and `BASE` to the actual selected manifest
digest. Set `IP` and `EXPIRES` from the concrete approved VM and cleanup deadline.
Do not paste tokens into shell history or source files.

```sh
python3 demo/portable-gitops/stack.py prepare --state "$STATE" \
  --inputs "$INPUTS" --private-ip "$IP" --expires-at "$EXPIRES" --base-manifest "$BASE"
bash demo/portable-gitops/acquire.sh "$STATE"
python3 demo/portable-gitops/stack.py services --state "$STATE"
python3 demo/portable-gitops/stack.py wait --state "$STATE"
python3 demo/portable-gitops/stack.py configure --state "$STATE"
python3 demo/portable-gitops/stack.py cluster --state "$STATE"
python3 demo/portable-gitops/stack.py credentials --state "$STATE"
```

Acquisition verifies the official version-specific K3s/Runner checksum listings;
it records downloaded bytes and resolved container digests. These are version
selections and actual acquisition receipts, not independently signed release
attestations. Services are never started by the acquisition step.

`configure` creates a private GitLab project and scoped project runner. The real
pipeline builds offline, generates a Syft SBOM, scans against the retained genuine
Grype database, enforces the exact exception, pushes the image to the authenticated
registry and writes a full deployment commit. The deployment points to the pushed
image digest. Each job publishes its actual artifacts in GitLab. There is **no image
signing stage** in this scenario. The project uses a trusted protected default
branch. The operator verifies protection before triggering CI. The Docker-capable
runner remains fully trusted on the disposable VM, as described above.

Publication checks that the daemon image config, build receipt, SBOM and actual
scan report still match. A retry reconciles the existing deployment branch/file
and uses optimistic file updates. Repeated app-image builds may reuse the same
chart; a changed chart requires an explicit reviewed deployment-chart update.
The lease, finding exception and database lifetime are rechecked before pushes
and commits.

Wait for the actual GitLab pipeline to succeed. `baseline` refuses a non-successful
pipeline and records its genuine ID/source revision. Partial stage failures retain
private diagnostics; inspect those before resuming. Do not rerun a mutating stage
blindly or replace its state directory.

```sh
python3 demo/portable-gitops/stack.py baseline --state "$STATE"
python3 demo/portable-gitops/stack.py prove --state "$STATE" --phase baseline
```

`prove` waits for the exact Argo Git revision, observes Deployment readiness and
runs real allowed/denied Kubernetes Jobs using the built image. Both Jobs must
resolve the same ServiceIP. The unauthorized connection must time out or be
refused while the positive control succeeds; HTTP errors, failed DNS and ambiguous
network errors are inconclusive. These are measured reachability controls
consistent with the declared policy, not a separate qualified causal proof.
Each attempt uses fresh Job names and a new receipt path, so failed evidence is
preserved. The allowed source has at most 15 seconds to establish the health
control; every startup attempt is retained and HTTP errors stop the check. The
denied source waits the same 15-second admission interval. Each role then requires
three consecutive final observations within its 25-second probe budget; one
unexpected result fails the attempt. This avoids mistaking a newly started Pod's
initial policy admission delay for the stable outcome. The resolved address must
match the actual Service address. Job UIDs, timings and all outcomes are retained
privately; use the `proof` path printed by the command.

Wait at least75 seconds for a complete60-second observation window. In a plugin
Python environment with its locked dependencies installed:

```sh
. "$STATE/credentials.env"
python3 scripts/observe_gitops.py collect --config "$STATE/observer-baseline.json" \
  --phase baseline --output "$STATE/observation-baseline"
```

Inject the fault through a new Git commit:

```sh
python3 demo/portable-gitops/stack.py fault --state "$STATE"
python3 demo/portable-gitops/stack.py prove --state "$STATE" --phase fault
```

Only `networkPolicy.metricsPort` changes9090→9091. The app and Service still listen
on9090, and health8080 remains allowed. Argo can remain Healthy/Synced and the
Deployment ready while Prometheus `up` becomes0. Wait75 seconds, collect the fault
phase and use the observer's comparison/report output. Do not label a missing
metrics series as recovery or infer full provenance from green controller status.

```sh
python3 demo/portable-gitops/stack.py recovered --state "$STATE"
python3 demo/portable-gitops/stack.py prove --state "$STATE" --phase recovered
```

The corrective Git commit restores9090. Wait75 seconds, then collect with
`--phase recovered --not-before <observed_at from the reported proof path>`.
The observer requires fresh post-boundary samples, increasing synthetic activity
and preserved runtime identities. Keep its aggregate assurance limitation visible.

## Cleanup and evidence

```sh
python3 demo/portable-gitops/stack.py stop --state "$STATE"
```

This stops only recorded units and the exact labeled GitLab container, even after
the authorization expiry. It retains private state and logs. The cloud owner must
then delete the exact approved resource group before its deadline and verify its
absence. Stopping these services is **not** cloud cleanup or a billing guarantee.

Record actual image/source/pipeline identities, three observation phases, both
NetworkPolicy controls and the cloud deletion receipt. The narration and recording
must show these real services and distinguish an actual completed run from these
unexecuted assets. No successful run is claimed by this README.

# Canonical runtime production

`factory.runtime_observations.RuntimeObservationSession` retains one concrete
owned driver, journal, package and original bootstrap authority. There is no
receipt loader or callback-selected collector. Every supplied intent and every
produced artifact remains bound to its exact bytes. Native selections use the
fixed `NativeDiagnosisSelection` and `NativeRecoverySelection` types; selections
are data and do not recreate collection authority after a restart.

## Phase composition

`factory.owned_runtime_inputs.OwnedRuntimeInputCoordinator` is the fixed
production caller. Its pure `bind_runtime_recipe(raw, identities=...,
environment_id=...)` parser validates a separately frozen selection before CI
writes. The selection fixes both workloads, container names, complete Argo
source mappings, health paths and expected body hashes, telemetry job and ports,
four probe peers, policy source path/scalar pointer, CNI descriptor and schedule.
The delivery source position is explicitly selected from the mission mapping.
UIDs, Pod addresses, Service addresses, images and delivered revisions come from
original current producers and direct reads. They are never guessed in recipes.

The selected CNI DaemonSet spec hash and container names are explicit
expectations. Actual reads must match them. The policy source supports one
strict JSON/YAML manifest or one Helm values document with an explicit string
pointer. The native Git producer independently renders the changed source and
checks the exact qualified policy delta; the runtime never infers a chart field.

The operation producer derives intent from actual delivery/readback, calls
`begin(context, intent=Path, intent_sha256=...)`, creates its authorized fixed
probe actors, then calls `start_probes`. `RuntimeCollectionSelection.window`
contains the immutable observation interval for their schedules. Baseline probes
are prepared after real CI publication in `build-promote`; replay probes are
prepared in `reset`. Fault and repair probes are prepared in the corresponding
`inject` and `deliver` mutation phases.

The read-only baseline and confirm-fault phases only finish their pending GET
collection and read the existing Pod logs. They never create Pods or execute a
command in a container. The fixed `dispatch` handler returns the actual
collection/probe artifacts. A measured phase alone emits no D1/D2 success.
The mutation result includes its deduplicated original runtime artifact closure,
including raw F0 observations and the original input/mapping files. Recovery
probe completion includes the full contract/evidence selection. Other producers
must separately include their CI, Git and Kubernetes operation evidence in the
overall phase result; an embedded filesystem path is not exported evidence.

`prepare_probe_actor` retains the original plan by identity. The owned Kubernetes
component calls `require_probe_plan(plan, context)` and regenerates its exact
manifest before creating it. Copied or deserialized plans fail this admission.
The actor is one fixed non-root container plus an immutable ConfigMap; its only
code runs the packaged HTTP probe against explicit scheduled requests. The
mission interpreter image must equal the original verified CI publication and
its fresh runtime-only image verification. Four actors are supported when the
health peer differs from the metrics peer. Pending scheduling permits only
bounded GETs before the first immutable request time; it never recreates Pods.

The root-owned `OwnedRuntimeTransport` derives actual endpoint, CA and scoped
read identity from original bootstrap objects. `read_transport` explicitly
selects the owned Argo and Prometheus Services. Logical configuration for these
three read providers must select `read_auth: {mode: none}` and omit `ca_file`;
actual credentials and CA come solely from the independently issued read
ServiceAccount. Original config bytes and the six run identities remain intact.
Each collection/probe retains original before/after transport handles.
Historical verification audits those original reads using the current phase;
it never renews an expired read token or trusts a saved transport projection.

Readback compares Pod spec, DownwardAPI environment, immutable script/config,
namespace identity/labels, uninterrupted container image identity, and exact
Service UID/ClusterIPs. Each bounded log record must match the original request,
actual Pod UID, schedule and observation interval. Negative probes retain their
connection failure; positive listening controls must bracket both denied sources.
The fourteen canonical receipts remain independent evidence, never aggregate
JSON assertions of success.

## Independent F0 experiment

`factory.network_policy_qualification.NetworkPolicyExperiment` is a separate
`f0-network-policy` attempt before the incident. It supports the currently
implemented plain HTTP path only: explicit disabled mesh enrollment and an
observed ready Calico, Cilium or RKE2 Canal controller whose installed spec
matches the selected pin. Other CNI/mesh paths remain unknown.
The selected actual environment may therefore need a different supported
protocol-specific implementation; a lock string cannot qualify that path.

`prepare` produces an inert plan from the exact baseline manifest and source
revision. It changes one existing metrics peer pod-selector value, preserving
the rest of the policy, its ingress restriction and all image declarations.
For each baseline/fault/restored stage, the caller executes the existing owned
resource transition, calls `start_observation` before collection, and later
calls `observe`. Exact policy UID/resourceVersion observations bracket the
window. Original runtime and probe observations must show health retained,
fresh exact-target source samples, denied sources and listening controls.
CNI, namespace enrollment, other policies, Pod configuration and container
identities must remain unchanged across three strictly ordered windows.

Only those original observations can issue `QualifiedNetworkPolicyPath`.
`require_qualified_network_policy_path` audits the same process, runtime, run,
input identities and authorization expiry. The binding includes the exact
source path/revision, selector pointer, before/after values, policy spec hashes
and observation hashes. Primary incident receipts cannot issue this capability.
The caller must use explicit provision/probe grants in build-promote or reset;
this source implementation grants no permission for live actions.
The concrete caller observes the actual Argo-owned policy and namespace, rejects
Argo automatic self-healing, and invokes only the bootstrap's narrowly admitted
selector transition with original UID and resource-version safeguards. It never
disables a controller or adopts arbitrary resources for mutation or cleanup.

## Host verification and origin composition

`prepare_recovery` builds the contract from original intents and probe receipts;
it requires explicit image mappings and the complete native/delivery/inventory
chain. It does not invent missing CNI facts, image identities or receipts.
`bind_recovery_inputs` freezes the complete selection for the native report.
The native report remains an offline interpretation. The host `verify` phase
separately consumes original collection, bootstrap, probe, image, native,
correction and F0 capability objects.

The closed `canonical-runtime-v1` provenance adapter accepts only an original
process-local `RuntimeObservationOrigin`. It reports exactly the assurances
whose original concrete capabilities have been checked; missing capabilities
remain required gaps. All nine assurances are required for complete recovery.
Controlled collector/clock/probe fixtures cannot issue live qualification.

The correction producer retains original successful validation only after final
scope/native/output checks and terminal publication. Its fixed
`require_validated_correction` accessor exposes the original scope and native
origin plus exact patch, contract, correction and rendered artifact selections.
A consumed validation claim, failed validation or saved diagnostic cannot
restore that success.

Fresh `up=0` source samples can prove the expected fault even when the blocked
application counter disappears. The exception is limited to known counter
absence gaps after exact target, raw source timestamps, locked count/window and
completion freshness checks. Denied APIs, wrong targets, missing up evidence,
changed images and all baseline/recovery counter requirements stay incomplete.

Live acceptance still requires actual selected images and trust material,
authorized provider and cluster access, the independent F0 run, original Git
delivery origins, real primary/replay executions, continuous recording and
playback verification. Local tests establish none of those live results.

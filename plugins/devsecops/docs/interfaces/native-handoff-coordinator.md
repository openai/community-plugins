# Fixed correction handoff coordinator

`factory.native_handoff` supplies a narrow composition seam between the canonical
lifecycle's correction pause, the existing native driver, and the existing local
correction validator. The separately registered `owned-linux` operation backend
provides read-only readiness diagnostics when explicitly selected with
`--operation-backend owned-linux`, `--plugin-id` and `--plugin-version`.
The default backend remains unavailable. The full factory workflow and native
phase composition remain unimplemented; the readiness handler returns incomplete
without reserving infrastructure. See the [owned Linux backend contract](owned-linux-backend.md).
Component tests never establish a canonical run.

## Explicit host input preparation

The future fixed operation backend calls `prepare_correction_scope` before
requesting either `primary-correction` or `replay-correction`. The arguments are
explicit trusted host selections: repository, full immutable base commit,
external profile and correction contract with independently selected SHA256s,
required executable paths and SHA256s, trusted local schema directory and its
existing `_schema_receipt` digest, a new private output directory, and a bounded
timeout. No input JSON selects an executable, module, callback, validator or
command. Tool downloading is outside this API. The existing Git snapshot helper
uses the trusted host Git on PATH; this component does not claim independently
pinned Git executable provenance.

Required tools are exactly kubeconform plus Helm and/or Kustomize when the selected
profile uses them. Paths are absolute, without symlinks, and outside the candidate
repository. The contract must bind the exact base and profile. Preparation uses
the existing selected-input Git snapshot code, preserving actual committed
source bytes. It permits at most 59 selected source files so the four fixed
metadata inputs and source files fit the existing native request bounds.
Preparation requires the exact worktree root with ordinary in-tree `.git`
metadata. Linked/common Git layouts, alternates and symlinked object storage are
unsupported. Ambient temporary storage must remain outside the source checkout;
configured candidates are checked before tempfile probing or Git execution and
the selected scratch root is rechecked during validation. These restrictions
preserve source files and metadata without adding support for new Git layouts.
Containment also compares source-ancestor device/inode identities, including
case-insensitive path aliases. With an uncached tempfile root, preparation probes
external candidates in order and stops at the first usable directory. Running
from the source checkout is supported when that external root is available;
the working-directory fallback is rejected only if it is actually reached.
Explicit or cached temporary roots inside the source remain unsupported.

The returned opaque `CorrectionScope.input_artifacts` mapping includes the exact
profile, contract, selected source manifest, a source-name mapping, and every
source file. The backend supplies that mapping directly to the existing native
request API. A source path stored in evidence does not become an execution path.
The scope is process-local, has no public constructor or JSON/pickle loader, and
establishes only the host's selected inputs; it grants no deployment authority.
Preparation runs outside `engine.advance()`: both use exclusive main-thread
deadline contexts. The future driver's `native_inputs` method returns an already
prepared scope's mapping and fails closed when that scope is unavailable. It must
not invoke preparation beneath the engine's active deadline timer.

## Correction-only dispatch

Create `NativeCorrectionCoordinator(engine)` with the actual `LifecycleEngine`.
After the engine yields the bound correction request, call:

```python
origin = coordinator.execute(
    prepared_scope,
    request_path=Path(handoff["request_path"]),
    request_sha256=handoff["request_sha256"],
    expected_client_sha256=independently_pinned_codex_binary_sha256,
    native_options=explicit_host_native_options,
)
```

Native options use the existing closed native-provenance contract. The caller
cannot supply a validator. Package identity must equal the lifecycle identity.
The request must be the exact pending primary/replay correction, with no active
mutation or abort, while the environment is reserved. All six lifecycle inputs
are revalidated. The journal's execution mode and driver must match the engine;
controlled execution is always forwarded with `_controlled_test=True`.

Dispatch holds the existing lifecycle lock and rechecks the exact request. It
compares every frozen request input against the directly prepared scope. The
total deadline is bounded by 900 seconds, the remaining request interval, and
the remaining actual UTC operator authorization. Existing native and local tool
process cancellation remains in force under the outer synchronous deadline.

Before native invocation, a new deterministic per-request private directory and
fsynced `intent.json` establish an exclusive invocation claim. An existing claim
blocks retry, including a crash before it is known whether native execution
started. No success or nonexecution is inferred from directory existence.

The packaged `_CorrectionValidator` accepts exactly one patch. It uses the exact
received bytes with `correction.validate`, retaining its exact before/after render
inventories. The original selected source tree must match. Only a complete local
pass with preserved images and approved scope passes journal validation. Tools,
schemas and prepared inputs are rechecked before and after validation and after
origin publication. Raw source/patch/render contents remain in private artifacts.
The model's report cannot replace an independent validator result.

## Integration contract

`execute_validated_correction` already accepts the native result into the journal.
Therefore, after the coordinator succeeds, the caller invokes **`engine.advance()`
without `native_result`**. The existing `native-validation-recovered` path records
the accepted correction phase; submitting the result again is double acceptance.
The coordinator deliberately does not advance, deliver, clean up, or release the
environment itself.

The coordinator retains the actual `NativeExecutionOrigin` under exact run ID,
attempt, and request SHA. `coordinator.origin(attempt=..., request_sha256=...)`
returns that original object. The future fixed `validate-correction`, `deliver`,
and recovery handlers must explicitly require the relevant live object before
doing dependent work, and must still obtain their own authority/evidence.

Journal acceptance can succeed before provenance publication fails. In that case
the coordinator raises and retains **no origin**. The caller must stop dependent
delivery even though the journal contains validated artifacts. Reopening the
journal, creating a new coordinator, forked processes, copied files, or editing
saved `execution_mode` fields cannot restore the lost object. No automatic
invocation retry, journal rollback, or reauthentication from saved JSON exists.
Current-host/manual handoffs still require a separately qualified host-origin
adapter; this module does not convert them into direct native execution proof.

Review, diagnosis, and recovery native workflows remain outside this coordinator.
The direct correction origin authenticates only its existing five native byte
artifacts. It does not authenticate collectors, Git delivery, image mapping,
network policy enforcement, operator intent, or a complete recovery verdict.

## Remaining lifecycle obligations

| Canonical phase group | Existing component | Required backend work |
|---|---|---|
| Preflight | `doctor.inspect`, strict package/input checks | Real service grants, qualified lock and capacity; local doctor stays unknown |
| Prepare/provision | `RetainedTCGDriver` and provision scripts | Complete Big Bang services, qualified readiness and owned-resource inventory |
| Build/promote | `qualify_publish_and_propose`, exact GitLab pipeline collector | Bind real required job/runner/source and registry evidence; reports cannot restore capabilities |
| Baseline/confirm/verify | Runtime collection, diagnosis and recovery APIs | Same-run live observation origins and all required D1/D2 assertions |
| Native correction | This fixed coordinator and `execute_validated_correction` | Live qualification and direct origin consumption by dependent handlers |
| Other native workflows | `execute_native_codex`, journal artifact validation | Fixed workflow-specific validators and required actual execution proof |
| Validate/deliver | `correction.validate`, deployment proposal/commit components | Native correction delivery to exact reviewed tree; bounded Git write/readback and controller revision |
| Inject/reset/platform dependency/probes | Closed scenario and evidence contracts | Actual scoped mutations and independent observations; explicit unknown effects |
| Supply-chain/evidence-gap cases | Trust/scanner/collection components | Same-run negative controls and restored-evidence assertions |
| Export/cleanup/finalize | Journal/tail engine, TCG down observations | Bounded evidence export and all factory resources independently absent; TCG absence alone is insufficient |

An unknown mutation still blocks further effects and cleanup until independent
reconciliation. Native pauses preserve ownership. No fixture, local component
pass, accepted native artifact, or lab-only cleanup closes D1–D6 or records the
full repeatable demo.

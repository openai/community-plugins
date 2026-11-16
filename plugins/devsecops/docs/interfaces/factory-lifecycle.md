# Canonical factory lifecycle engine

`devsecops.factory.lifecycle.LifecycleEngine` advances the existing
`ironbank-mission-recovery-v1` scenario. It does not add a second scenario or a
second state store. The 33 phases, their order, D1–D6 assertions, primary attempt,
one reset, one replay, and four native workflows per attempt must equal the
installed canonical documents. A schema-valid edited graph is still rejected.

The fixed `owned-linux` backend composes the selected bootstrap, service, CI,
Git, runtime, native, case and cleanup producers. Its presence does not establish
live qualification. Without an explicit operation backend the default returns
`incomplete`, exit 2, before reserving an environment or claiming work or cleanup. A Python test driver is permanently
marked `controlled-test`; even all 33 completed fixture phases cannot produce a
canonical success or exit 0. There is no user-provided command, module import,
validator name, or executable script in the configuration.

## Packaged command

The console command and `python /installed/devsecops/scripts/factory_demo.py`
use the same engine. The following starts a new journal with explicit operator
inputs. With the default unavailable operation backend shown here, it returns
exit 2 with `operation-driver-unavailable` and performs no provisioning:

```sh
factory-demo e2e --profile bigbang-live \
  --config /private/operator/demo-config.json \
  --lock /private/operator/qualified-lock.json \
  --environment owned-lab-name \
  --environment-inventory /private/operator/environment.json \
  --output /private/evidence/new-run \
  --driver current-host --cleanup always
```

The default package identity comes from `SHA256SUMS` in the installed plugin.
`--package-manifest` may select an explicit candidate manifest for development;
that does not establish installation or publisher authenticity. A source checkout
without a manifest fails rather than making up a package identity. The default
shared lock directory is `~/.cache/devsecops/factory-locks`; operators using one
environment must use the same supported lock directory.

On resume, replace `--output` with `--resume` and supply **all the same explicit
config, lock, profile, environment and inventory inputs again**. Include the same
`--package-manifest` and `--shared-lock-dir` when overriding their defaults.
An actual native pause additionally requires `--native-result /absolute/result.json`.
Use `--abort` on a resumed run for explicit cancellation. These two options are
mutually exclusive and invalid for a fresh run. `codex-cli` is the fixed resident native driver and requires `--operation-backend
owned-linux`, an explicit `--native-selection` path and SHA-256, and the complete
pinned production selections described in [auxiliary inputs](lifecycle-auxiliary-inputs.md).
It runs the accepted native steps in one process. `current-host` retains the
manual yield/resume protocol; importing results does not reconstruct the direct
resident authority required by production delivery. Neither flag selects an
arbitrary operation backend. Neither route is live-qualified by local tests.

## Python entry points

```python
engine = LifecycleEngine(
    journal,
    inputs={
        "scenario": scenario_path,
        "assertions": assertions_path,
        "config": operator_config_path,
        "lock": operator_lock_path,
        "package": installed_sha256sums_path,
        "environment": environment_inventory_path,
    },
    shared_lock_dir=operator_shared_lock_directory,
    driver_id="unavailable",
)
result = engine.advance(native_result=None)
```

`scenario`, `assertions`, `config`, `lock`, and `environment` also accept
explicit trusted JSON dictionaries. Their canonical bytes use sorted keys,
ASCII escaping, compact separators, and one trailing newline. Files preserve
their exact bytes. `package` requires an explicit regular-file Path:
`package_sha256` means **SHA-256 of the installed SHA256SUMS content manifest**,
not a version string or ZIP digest. Verifying actual package membership and
file hashes remains a separate required concrete preflight check. A manifest's
existence or hash does not establish that verification.

All six input hashes must equal `RunJournal.identities`. The lock must bind the
same scenario and assertion bytes and profile. Config `environment` and the
explicit environment inventory's `environment_id` must match the journal. The
inventory's authenticity and resource observations are not proved by hashing it.
The runner freezes the exact bytes and requires independently supplied inputs
again on every resume. It never obtains operator authority from embedded paths.
The caller must not reconstruct trusted input arguments from untrusted evidence.

`advance()` runs until a native pause, unresolved operation, incomplete cleanup
tail, or terminal result. Repeating a completed run returns its existing result;
it cannot create another replay. There is no phase-selection or skip argument.

`advance(native_result=explicit_path)` delegates the exact pending result to
`RunJournal.accept_native` and the concrete driver's deterministic validator.
Run/request/step identity, all six hashes, expiry, result location, artifact
membership, bounded bytes, and artifact digests remain mandatory. Wrong or stale
results do not advance, silently cancel, or clean up the paused environment.

`interrupt()` is explicit operator cancellation. It records the interruption,
cancels an outstanding native request if present, and enters the mandatory tail.
A native pause itself is not cancellation. Unresolved mutation effects continue
to block cleanup until reconciliation. Interruption holds the lifecycle lock
before changing the journal and through continuation, taking the environment
mutation lock only within that guard. A competing native runner therefore causes
interruption to fail before altering its request, phase or evidence. An unstarted
run is rejected without reserving an environment; an unfinished read-only
preflight is recorded interrupted without a reservation or cleanup invocation.
Finalized runs return their existing result without another transition.

`reconcile(operation_id=..., outcome="applied" | "not_applied",
receipt_artifacts=[...], validator=trusted_function)` requires independent
observations and a trusted validator of `(outcome, frozen_receipt_bytes)` returning
completed check IDs. It freezes bounded receipts before validation, checks them
again afterward, and journals the validation and observed effect. It never
re-executes the interrupted operation, even for `not_applied`. It retains the
original failure and moves toward export/cleanup. An interrupted cleanup may be
resolved from validated deletion observations; it is not repeated blindly.

## Closed operation-driver contract

Concrete adapters implement these Python methods; they are selected by code in
the package, never dynamically loaded from JSON:

```python
execute(PhaseContext) -> PhaseOutcome
native_inputs(PhaseContext) -> Mapping[str, Path]
validate_native(request, result, artifact_bytes_by_name) -> list[str]
validate_cleanup(tuple_of_frozen_receipt_bytes) -> list[str]
completed_native_artifacts(step_id) -> tuple[Path, ...]  # fixed owned driver
```

`PhaseContext` contains the run ID, six identities, immutable phase and input
snapshots, journal-owned frozen input references, run directory, execution mode,
and monotonic deadline. `remaining_seconds()` supplies the remaining I/O budget.
Drivers must use the approved scope, bound all subprocess/network operations,
cancel and reap subprocesses, and propagate cancellation. They must not catch
the engine's timeout signal or continue work in detached threads/processes.

`PhaseOutcome(status, code, artifacts=(), assertions=(), mutation_effect="unknown")`
uses `completed`, `incomplete`, `error`, or `assertion-failure`. Codes are bounded
identifiers, never raw exception or provider text. Completed operations require
independent evidence files. Phase and mutation results allow at most 256 files
including the lifecycle receipt, with unchanged 16 MB per-file and 64 MB aggregate
limits. Intent and native request inputs remain limited to 64. A JSON `success: true` is not an outcome or proof.
Assertions are typed `AssertionResult(case_id, assertion_id, status)` entries
from that phase's canonical case IDs, with `pass`, `fail`, or `unknown` status.
Evidence interpretation belongs in the reviewed concrete adapter, not an LLM's
unstructured response. Any failed assertion retains a failure; missing required
assertions prevent success. D1 and D2 require separate primary and replay proof.
Validators interpret their supplied frozen bytes without independent network
calls. Export and finalization are local evidence operations. Adding external
publication or additional effects requires an explicit contract extension.

The runner journals mutation intent before `prepare`, `up`, `build-promote`,
`inject`, `deliver`, authorized probe creation, the platform-dependency fault,
`reset`, and `down`. For the fixed owned runtime recipe, the original producer
creates scheduled actors under build/delivery/reset intents. The later `probe`
phases only read those actors' logs and measurements: they still require the
explicit probe grant, cannot start a second operation, and must report
`not_applied` for new mutations. Other drivers retain the original probe-mutation
contract. It verifies the corresponding config permissions; build and
promotion require both permissions. Ordinary observations, supply-chain checks,
and evidence-gap checks are read phases, not fictitious mutations. Their future
handlers may not add external writes under this contract. An implementation
needing additional effects must extend the closed operation contract explicitly.

On macOS/Linux, main-thread POSIX timers interrupt bounded synchronous adapter
calls and restore the previous signal handler. A conflicting timer or an
unavailable main-thread execution context fails before starting a mutation.
This is not a sandbox for arbitrary Python or a guarantee that a remote write
was cancelled: any timeout or exception after mutation intent remains an unknown
effect and requires reconciliation. Per-handler transport/process limits remain
mandatory. No abandoned worker is allowed to race cleanup.

## Journal, authorization, and failure boundaries

The existing hash-chained RunJournal remains authoritative. Optional lifecycle
state adds frozen input references, driver/mode identity, ordered phase records,
the active phase, original abort cause, and derived final result. Foundation
journals without lifecycle state remain valid. Every load rehashes immutable
events and referenced artifacts; semantic-validation caching applies only to
the identical previously validated event bytes. This is integrity bookkeeping,
not external authentication or a distributed lease.

A local lifecycle lock serializes execution without reserving infrastructure.
The closed read-only preflight runs under that lock. Its failure preserves
evidence without an environment reservation, cleanup claim, or terminal success.
After preflight succeeds, the existing environment lock and persistent owner
reservation apply. Native exit 3 and unresolved mutations preserve that owner.
Callers on different hosts still need a shared supported ownership mechanism;
independent local lock directories do not provide one.

The config expiry is checked against actual UTC before every operator external
or native phase and after a native pause. The phase budget is also capped by
remaining authorization. An expired pause is cancelled and recorded incomplete;
it cannot authorize later deployment work. Export and owned-resource cleanup
remain permitted after expiry. Cleanup must be authorized before preflight can
advance into provisioning. Controlled fixtures alone use the injected test clock.

Completed phases cannot skip predecessors. A failure/interruption switches only
to the declared `export`, `down`, `finalize` tail. The original failure is
immutable: cleanup errors never overwrite it. A crashed active phase is not
automatically repeated. A mutation with an unresolved effect blocks all further
external effects, including cleanup. Cleanup remains owed, rather than falsely
marked completed.

Terminal results require completed evidence export, observed cleanup validated
by the cleanup adapter, released environment ownership, and finalization.
Failed export or cleanup stays nonterminal/incomplete and retains receipts; this
slice does not silently retry failed tail operations. Operator reconciliation
and a concrete cleanup path remain necessary. Final decisions are derived from
journaled outcomes and the canonical assertions, never supplied as JSON flags.

| Result | Exit | Terminal | Meaning |
|---|---:|---|---|
| `awaiting_native_step` | 3 | No | Return control to the current host; preserve environment |
| `reconciliation_required` | 2 | No | Unknown prior effect; no automatic retry or cleanup |
| `cleanup_required` / `incomplete` | 2 | No | Required handler, evidence, export, or cleanup is missing |
| Final `error` / `incomplete` | 2 | Yes | Tail completed; required execution/evidence did not |
| Final assertion `fail` | 1 | Yes | Completed retained assertion failure after export and cleanup |
| Final assertion `pass` | 0 | Yes | All canonical live obligations satisfied by an actual registered driver |

The final row is unreachable with the currently registered drivers. Controlled
fixtures retain their mode in every phase, native request's containing journal,
runner receipt, and final result; fixture success is never live qualification.

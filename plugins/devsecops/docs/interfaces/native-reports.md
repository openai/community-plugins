# Fixed native report handoffs

`factory.native_reports` services the review, diagnosis and recovery report
handoffs in a concrete, resident `LifecycleEngine`. It does not register a
backend, advance the lifecycle, deliver a correction, or enable a public native
CLI mode. Primary and replay have separate request identities. The existing
correction coordinator and patch-specific `NativeExecutionOrigin` are unchanged.

A completed native execution may produce a `pass`, `fail` or `unknown` domain
report. The independent validator preserves that decision and every substantive
gap; completing this handoff does not satisfy a scenario assertion or establish
runtime recovery. The factory doctor's separate component check is outside this
API. The canonical scenario contains eight report/correction native handoffs.

## Explicit host scopes

Prepare one opaque `NativeReportScope` with the matching fixed function:

```python
prepare_review_scope(
    repository=repository, base_commit=exact_base, head_commit=exact_head,
    profile=external_profile, profile_sha256=profile_hash,
    tools={"helm": (helm_path, helm_hash), "kubeconform": (validator_path, validator_hash)},
    schemas=local_schemas, schemas_sha256=schema_hash,
    output=new_private_directory, deadline=absolute_monotonic_deadline,
)
prepare_diagnosis_scope(
    baseline=baseline_path, fault=fault_path,
    output=new_private_directory, deadline=absolute_monotonic_deadline,
)
prepare_recovery_scope(
    baseline=baseline_path, fault=fault_path, recovered=recovered_path,
    contract=contract_path, contract_sha256=contract_hash,
    evidence=closed_evidence_path_mapping,
    output=new_private_directory, deadline=absolute_monotonic_deadline,
)
```

All paths are explicit absolute host paths. Scope preparation freezes raw files,
not parsed/reserialized substitutes, into a new private directory. Its
`input_artifacts` mapping is passed directly to the existing journal request.
Saved selection documents are useful evidence, not constructors for a scope.
The scope and eventual validated execution are bound to the preparing/issuing
process and cannot be pickled or loaded from JSON.

Preparation requires a future absolute monotonic deadline no more than 300
seconds away. Its bounded reads, source snapshots and tool hashes cooperate with
that deadline. It does not establish a nested POSIX signal timer: a lifecycle
`native_inputs` handler already runs inside its phase timer. A standalone host
caller must likewise provide its own bounded synchronous execution context when
filesystem responsiveness needs an enforced interrupt. The existing correction
scope preparer retains its separate, non-nestable deadline contract.

Native inputs retain the journal's 64-reference, 64 MB aggregate and 16 MB per-file
bounds. The scope selection counts toward those bounds. Recovery accepts only the
existing closed `verify_recovery` evidence names and the selected contract's
`http:<case-id>` names; absent optional evidence stays absent. Embedded source
paths, credentials, URLs, origin claims and commands are never followed.

Review initially accepts exact local commit IDs only: no mutable worktree,
remote ref lookup, GitLab call or credential selection. It freezes and audits both
selected render input closures, the external trusted profile, actual tool hashes
and the local schema-set hash. The repository uses ordinary local Git metadata;
external object storage and unsafe temporary roots are refused. Required tools
are exact `(absolute_path, sha256)` pairs; no renderer or validator PATH fallback
is used. Git remains the existing local snapshot adapter. Review scope preparation
keeps its new files outside the selected source checkout. The host must choose
an output directory outside the installed package; execution audits the complete
installed package before invoking the native client and never writes into it.

## Direct execution and independent replay

Construct `NativeReportCoordinator(engine, selection)` with the existing frozen
`NativeCodexSelection`, then call `execute(scope, cancel=optional_event)` for the
pending matching canonical report request. The coordinator holds the lifecycle
lock, checks the actual engine/run/step/six-input identities and exact frozen
scope, and writes an exclusive fsynced per-request invocation claim. A failed or
ambiguous invocation is not automatically retried, even in another coordinator.

One deadline covers native execution, replay and publication: at most the selected
900-second limit and the actual request/config expiry. Up to one third of the
remaining budget, capped at 120 seconds, is reserved for independent validation.
The model receives only the earlier portion. Cancellation uses the existing
bounded process-group runner; no broader policy, MCP access, fallback model,
installation, HOME/CODEX_HOME change or remote operation is introduced.

The concrete Codex driver must return a real complete single-turn trace and exact
retained manifest. Declared artifact kinds, names, paths, sizes and hashes are
joined to those exact retained bytes. Duplicate candidates are rejected. Require
exactly one structured report of the workflow's required kind, plus at most one
`supporting-evidence` HTML companion. The HTML must equal the installed renderer's
output for the validated structured report. A narrative or HTML summary cannot
replace the report JSON.

Independent replay calls the exact installed `scripts/devsecops_review.py` with
the approved external Python in isolated mode. It uses only fixed arguments,
scoped private temporary storage, a minimal credential-free environment, bounded
stdout/stderr and the remaining deadline. Package, client, interpreter/dependency,
source/tool/schema/input and output identities are rechecked through terminal
publication. Actual stdout, stderr, invocation and independent reports remain
private evidence.

Review comparison covers the complete supported report semantics, including
scope/revisions/profile/policy, source manifests, tool/schema identities, global
and target gaps, findings/changes, exceptions, render hashes, schema counts/results,
summary, decisions and limitations. Only run IDs, observation/evaluation timestamps
and independent process receipt variability are normalized. Process receipt
shapes and bounds are checked; operation/tool/target/side/attempt/exit status are
compared, and render stdout hashes/lengths are compared. Raw lint/validator stdout
hashes and sizes and elapsed times remain retained child observations; differing
scratch paths make them unsuitable as equality proof of independent executions.
They do not establish that the child invoked each individual tool. Full replay
establishes the matching result. Unordered schema results are sorted without
deduplication. Unknown substantive fields are not silently discarded.

Diagnosis uses its full schema-valid, sealed deterministic report. Recovery uses
its full schema-valid, sealed offline semantics, excluding only the observation
time and corresponding seal after independently validating both. Child observation
times must fall inside its actual execution. Valid unknown reports remain valid;
`runtime-command-error`, missing outputs and malformed reports are rejected.

Recovery freshness is recalculated with the real current clock before journal
acceptance, after acceptance and after terminal publication. Substantive drift
rejects the result and never backdates the validator or reruns the model. Review
policy exceptions are date-based; crossing the UTC policy evaluation date likewise
refuses acceptance conservatively. No production call uses `now=` or test provenance.
Request/config expiry is separately checked against actual UTC as well as the
monotonic deadline. A late publication failure withdraws only this invocation's
terminal marker through its retained directory descriptor.

## What the result proves

`ValidatedNativeReport.document` is a narrow `native-report-execution` receipt:
actual native report production plus independent local artifact validation. It
binds exact request/result/report/validation/trace/manifest/replay bytes and the
configured client/model/package identities. It is not a correction origin,
delivery capability, collector authentication, or recovery approval. Controlled
lifecycle fixtures remain permanently `controlled-test`.

`coordinator.report(step_id=..., request_sha256=...)` returns the original object
only after rechecking retained evidence bytes and the engine's six inputs. A new
coordinator cannot restore it from accepted journal records. Publication may fail
after journal acceptance; such a run has no returned object and must not be
advanced as though its provenance were available.

The child recovery command receives evidence only and cannot authenticate live
origins. The later parent verification must independently consume actual
correction, delivery, runtime, inventory and probe capabilities. The recovery
provenance registry remains unchanged and empty until that separate fixed live
composition is implemented. This slice does not claim complete canonical factory
qualification or provide the later resident multi-workflow dispatcher.

## Tests

Tests replace only native model execution with an explicitly controlled boundary.
They run the actual installed-copy command with real Helm/Kubeconform and realistic
runtime evidence, preserving `fail`/`unknown` outcomes. Focused regressions cover
artifact joins, scope and tool/source pins, forged reports/gaps, replay cancellation,
real-time freshness/authorization expiry during publication, immutable retry claims,
changed retained evidence and non-deserializable process-bound results. These tests
are local component evidence; they are not a live model, cluster or customer run.

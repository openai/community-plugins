# Direct native correction provenance

`devsecops.factory.native_provenance.execute_validated_correction` composes the
concrete native Codex driver with `RunJournal.accept_native` and a separately
trusted deterministic artifact validator. It emits the v2 native execution
document and returns an opaque `NativeExecutionOrigin` only after that sequence
completes. It never upgrades imported reports or copied JSON to origin authority.

```python
origin = execute_validated_correction(
    journal,
    request_path=pending_request_path,
    request_sha256=pending_request_sha256,
    attempt_id="primary",
    environment=owned_environment_id,
    expected_client_sha256=qualified_codex_executable_sha256,
    validator=fixed_correction_validator,
    provenance_output=new_private_directory,
    native_options={
        "installed_root": installed_plugin,
        "package_sha256": package_manifest_sha256,
        "plugin_id": installed_plugin_id,
        "model": configured_model,
        "reasoning_effort": configured_effort,
        "expected_client_version": "codex-cli 0.167.0-alpha.3.2",
        "executable": approved_codex,
        "engine_python": approved_external_venv_python,
        "total_timeout": 900,
    },
)
```

`journal` must be a concrete owned journal with exactly that pending correction
request, run identity, environment, and package. Other workflows and the factory
doctor component cannot mint correction provenance. The Codex executable hash is
an independent caller input from the qualified tool lock, checked before model
execution and against the actual retained invocation. All other native driver
limits and policy controls remain in force.

`validator` is executable **trusted-host code**, not a module name or path selected
by config, repository contents, request JSON, the model, or any receipt. The
production backend must choose a fixed correction validator in code. Its function
bytecode/constant identity and source-file hash are retained and rechecked. This
identifies the selected implementation, not all its transitive dependencies or
closure policy. The validator must consume the supplied frozen request, result,
and artifact bytes and perform independent semantic checks with its own bounded
tools. It returns the nonempty check identifiers required by `RunJournal`; that
return value never asserts a scenario pass. Validator failures leave the request
pending and are reported without raw diagnostics. The original frozen input bytes
are checked on both sides of validation and before issuing the origin. A supplied
cancellation event is also checked through validation and publication; cancelling
after the CLI exits cannot silently produce a new origin.

The native process's bounded total deadline does not sandbox an arbitrary Python
validator callback. Request validity is checked on both sides of that callback;
the fixed production validator must enforce its own execution/time limits. A
successful handoff acceptance is written to the journal before origin publication.
If subsequent publication fails, no origin is issued and delivery must stop even
though artifact acceptance remains recorded. The wrapper does not roll back the
journal, repeat the model, or silently recreate authority from files.

## Version 2 identity

[runtime-native-execution.schema.json](../../schemas/runtime-native-execution.schema.json)
accepts the historical v1 shape and the explicit v2 `codex-cli-single-turn` shape.
V1's `host_id` / `turn_id` remains compatible for historical offline fixtures.
V2 records:

- A newly generated execution UUID and a local host identifier derived from the
  actual host name, without claiming hardware attestation or publisher ownership.
- The observed thread ID and ordinal `1`, checked against exactly one started and
  completed turn in the retained real CLI JSON trace.
- `server_turn_id: null`. CLI 0.144.3 does not expose that server ID; null is allowed
  only by this explicit v2 single-turn driver variant. A fabricated non-null ID or
  a second turn fails the schema/trace checks.
- Configured model and reasoning effort, client version, independently pinned
  executable hash, and external Python/import identity hash.
- Run/attempt/environment and all six original identities; exact request,
  result, patch, package, trace, invocation, native execution receipt, and terminal
  execution-manifest hashes.
- The exact independent validator identity and journal validation receipt hashes,
  together with ordered execution and validation timestamps inside the request
  window.

Configured model/client identity plus the actual native trace satisfies the
canonical identity requirement. Provider-observed model identity and a private
server turn ID are not invented or added as new acceptance gates. Independent
artifact checks, actual delivery, and recovery remain separate required evidence.

The private directory retains `execution/` from the concrete driver, the validator
identity, exact native artifacts, and an `origin-manifest.json` binding their bytes.
Files and directories preserve the native driver's private and anchored publication
rules. The v2 document's scope is permanently
`native-execution-and-artifact-validation-only`. Its JSON bytes alone prove no
origin, even if an imported document says `execution_mode: direct`.

## Recovery consumption and limits

The direct caller may pass the original opaque object to:

```python
verify_recovery(..., native_execution_origin=origin)
```

The consumer matches exactly five frozen byte inputs: `native_execution`,
`native_request`, `native_result`, `native_validation`, and `patch`. It authenticates
only the `native-execution` assurance and a separate condition for that assurance.
It does not authenticate operator intent, collectors, Kubernetes destination,
image mapping, correction-engine execution, Git delivery, probes, or network-policy
topology. No complete recovery provenance adapter is registered yet, so those
missing origins still keep the overall result unknown.

`NativeExecutionOrigin` has no public constructor, JSON loader, pickle/restore
path, or executable-code registration facility. Returning its document or hash
mapping produces detached data, not another origin object. It is process-local:
a restart or a current-host/manual handoff requires a separately qualified host
origin adapter; copying the retained files cannot replace that missing mechanism.
The eventual production backend must retain the issued object while composing
the canonical headless run or supply a separately engineered durable verifier.

The `_controlled_test=True` hook permanently marks the issued record and object
as controlled. Controlled objects never authenticate production, even when their
bytes match, and cannot be upgraded by editing an exported document. Recovery
also preserves the controlled marker in imported v2 evidence. Tests exercise real
synthetic subprocesses and journal validation with that marker; they are not
actual model or customer qualification.

# Final canonical evidence

The lifecycle publishes `evidence-manifest.json` and then `e2e-result.json` only
after its immutable journal has committed finalization and observed environment
release. Failed and incomplete terminal runs retain their actual derived result;
controlled execution never becomes canonical success. A preflight stop without
reservation is not a fabricated terminal run.

`terminal_evidence.publish_terminal` requires the original resident journal and
its lifecycle lock. The manifest binds the state projection, every immutable
journal event, and only schema-defined artifact references. It does not follow
paths embedded in opaque reports. References include cleanup observations and
validation, the six canonical inputs, the frozen auxiliary selections, operation
receipts and native outputs. Size, count, deadline, directory, file identity and
hash checks apply to the complete bounded set.

The publication graph is one-way: journal and artifacts → manifest → result.
Neither publication is appended back into the journal. Media and later human
review remain outside the finalized run. A completed manifest followed by an
interruption may be finished only if its exact bytes still match the finalized
journal. Existing publications cannot be replaced or silently revised.

`verify_terminal(run_dir, expected_result_sha256=..., expected_manifest_sha256=...)`
reads the fixed pair, replays the complete event hash chain with the normal state
reducer, checks the final projection and all registered bytes, and compares the
result to the journal-derived outcome. It never repairs a projection, resumes an
operation or restores a capability. Hash binding establishes consistency, not
independent authenticity or successful live execution.

The result contains the canonical phase and attempt records with their actual
start/end times, native references, cleanup disposition, package/config/lock/
scenario/environment identities and the manifest reference. Native identity is
nullable. A resident native session may supply the selected client binary/version,
model and effort only alongside its accepted receipts. Requested model and
provider-observed model remain separate; an unavailable provider identity is
`null`, never copied from the requested model.

Release qualification must separately establish the required real repetitions,
fresh environments, continuous recording, linked handoff identities, playback
and independent viewer acceptance. A locally verified result, component test,
packaged command or successful decode cannot satisfy those gates by itself.

Canonical phase and mutation observations retain at most 256 registered files, including their lifecycle receipt, with the existing 16 MB per-file and 64 MB aggregate limits. Intent and native request inputs remain limited to 64. Production native completion registers the resident session's original invocation, execution, trace, manifest, validation, and correction evidence; saved documents cannot restore the resident authority. The terminal manifest preserves each registered file hash and byte count.

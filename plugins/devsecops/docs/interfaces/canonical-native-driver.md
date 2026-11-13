# Canonical native drivers

`factory-demo e2e --driver codex-cli --operation-backend owned-linux` selects one resident `NativeFactorySession`. It services the canonical journal's eight handoffs in order: review, diagnosis, correction and recovery for primary, then the same four for replay. It never invokes the demo orchestration skill recursively. The operational backend must supply each scope from its retained, freshly checked source and runtime observations.

The CLI also requires explicit installed plugin identity/version and `--native-selection ABSOLUTE_PATH --native-selection-sha256 SHA256`. The selection is a strict JSON object with exactly these fields:

- `schema_version`: integer `1`.
- `kind`: `canonical-native-driver-selection`.
- `run_scope`: the six exact lifecycle input hashes.
- `environment_id`, `plugin_id`, `plugin_version`: the explicit lifecycle selection.
- `installed_root`, `executable`, `engine_python`: absolute host paths.
- `expected_client_sha256`, `expected_client_version`: the locked client bytes and complete supported version banner, such as `codex-cli 0.167.0-alpha.3.2`.
- `model`, `reasoning_effort`: the explicitly selected model and supported effort.
- `total_timeout`: each native invocation's bounded timeout, from 1 through 900 seconds.
- `max_trace_bytes`: the bounded retained trace size, from 1024 through 64000000 bytes.

The selected package must be the currently executing installed package. `executable` must equal `config.tool_paths.codex`; the unique `lock.tools` entry named `codex` must match both expected client values. The existing native driver verifies actual installed/client bytes, supported client behavior, isolated plugin loading, structured output and fixed engine replay. The selection's path and hash are auxiliary lifecycle inputs and must be resupplied unchanged on resume. They never select a callback, shell command, runtime authority or imported native result.

`NativePhaseCoordinator.prepare` consumes exactly `NativeSourceSelection`, `NativeDiagnosisSelection` or `NativeRecoverySelection`. These frozen objects describe pinned data. The fixed production coordinator revalidates the actual source/delivery or runtime collector origins before supplying them. Review and correction use the same actual fault source revision. Diagnosis and recovery freeze the collected bundles; recovery also freezes the exact contract and named evidence. The original scope and its external pins are checked again immediately before native dispatch. Native report replay remains offline and can report `unknown`; the actual host verification phase separately requires the retained direct runtime, correction, image, delivery and probe origins.

SIGINT and SIGTERM during the resident native loop set the shared cancellation signal. The fixed operation dispatcher observes this signal between phases. Native/session setup failures use the existing authorized cleanup tail. Unknown operation effects remain reserved for reconciliation. A completed terminal journal can be read back without a new model call. Accepted native history cannot resume in a new process: saved reports do not recreate direct origins. The resident interruption and cleanup controls can use the original retained objects. After process loss, a new `--resume --abort` invocation cannot reconstruct the full production cleanup capabilities; preserve the existing request, operation and ownership evidence for its supported inspection/recovery route. The abort CLI remains available for components with supported cleanup origins, but it is not a general full-factory recovery command. Establish a new qualified run only after the prior owned environment is safely reconciled. An unclaimed pending handoff also requires its original prepared scope or explicit fresh capability requalification; absence is an error.

`--driver current-host` keeps the exit-3 request/result protocol and never synchronously waits on the invoking model turn. Production **current-host resume after process exit is not implemented**: the new process lacks the original prepared scopes and direct execution capabilities. `--resume --native-result` therefore fails closed at fixed validation, including for the first report. A supported host capability requalification/continuation adapter is still required to close that path. JSON results are not a substitute. Controlled protocol tests demonstrate journal behavior only; they do not close this production gap.

`terminal_native_identity()` returns the requested client/model identity only after auditing retained accepted native receipts. `provider_observed_model` stays null because these receipts do not establish that separate fact. No native report grants runtime authority or makes a controlled run eligible for release recording acceptance.

The tests in `test_native_driver.py` call the canonical resident runner through all eight handoffs with controlled model/platform boundaries and real fixed replay/patch validation. `test_native_phase.py` exercises the real preparation functions at all four canonical input phases. Neither suite is evidence of a live end-to-end run, repetition, service recovery or playable recording.

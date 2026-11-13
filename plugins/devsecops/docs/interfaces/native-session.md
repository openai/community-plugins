# Resident correction session

`factory.native_session.NativeCorrectionSession` retains the original native
correction provenance while one Python process advances a concrete
`LifecycleEngine`. It is a callable component, not a complete factory backend or
a public `--driver codex-cli` mode. Only `primary-correction` and
`replay-correction` execute here. Review, diagnosis and recovery remain ordinary
native handoffs; this session cannot validate or authenticate those workflows.

## Explicit host selection

Create the session after the existing engine has reserved its environment and
yielded native work. Bootstrap and preflight stay with the existing engine.
Construct a frozen `NativeCodexSelection` with the installed package directory
and SHA256SUMS digest, exact `plugin@marketplace` ID, client executable and its
independently selected SHA256, supported exact client version, configured model
and reasoning effort, and external engine Python path. The API accepts no
commands, validator callbacks, arbitrary native options, report inputs or model
fallbacks. Selection alone grants no authority. The existing concrete driver
still verifies the installed registry/package, actual executable/client and
Python dependency identities, and retains its managed policy and sandbox rules.

The interpreter invocation path is preserved, including a venv's `bin/python`
symlink. No dependency installation, HOME/CODEX_HOME change, or writes to the
installed cache are added. Execution is bounded to 1–900 seconds and 1 KiB–64 MB
of trace, further limited by the request and actual UTC authorization expiry.
The supported client targets are inherited from `native_codex.CLIENT_VERSIONS`;
configured model identity is not a claim about an independently observed provider
model. See [native driver](native-codex.md) for the exact protocol and evidence.

## Resident handoff

The trusted host prepares an opaque `CorrectionScope` outside `engine.advance()`
and the fixed backend places its exact `input_artifacts` in the correction
request. Preparation and advancement both use exclusive main-thread deadline
contexts, so preparation must not be nested inside a phase handler. See the
[fixed correction coordinator](native-handoff-coordinator.md).

```python
session = NativeCorrectionSession(engine, selection=explicit_host_selection)
# The caller supplies a directly prepared scope for this pending correction.
origin = session.execute_pending(prepared_scope)
assert session.origin(attempt="primary") is origin
next_handoff = session.advance()
```

`execute_pending` derives the exact request path, hash and primary/replay identity
from the current journal. It invokes the existing fixed coordinator and its
independent patch validator, never a model-selected callback. It accepts the
result only once and returns the original `NativeExecutionOrigin`; it does not
deliver or advance. `advance()` then holds the lifecycle lock across origin
admission and the engine's existing reserved continuation, without another
`native_result`. Its existing `native-validation-recovered` transition records
the accepted phase. Unsupported pending workflows are left untouched.

Before any dependent advance or origin access, the session checks the exact run,
six inputs, driver object, execution mode, and accepted correction request,
result, validation and patch bytes against the retained origin. Primary and
replay origins stay separate. A future fixed delivery/recovery handler can call
`session.origin(attempt=...)` during that same `advance()` and must still require
its own delivery/runtime capabilities. This component cannot authenticate Git
delivery, live collectors, registry trust, network-policy behavior, or recovery.
Controlled test drivers remain controlled and never issue production authority.

## Failure, cancellation and process lifetime

All operations except `cancel()` run on the creating process's main thread.
Reentrant operations are refused. Each native invocation keeps the existing
fsynced, exclusive per-request claim; a failed or ambiguous attempt is never
automatically retried, even in a new session. An execution exception, deadline,
KeyboardInterrupt, or provenance-publication failure stops this session from
advancing or invoking again. In particular, journal acceptance before a failed
origin publication does not permit delivery.

`cancel()` may be called from another thread. It sets the event consumed by the
existing child-process runner, which interrupts and reaps its process group.
Cancellation is irreversible for this session and never claims platform cleanup.
It does not asynchronously terminate a platform phase already running inside
`advance()`; that phase retains the lifecycle's own deadline and reconciliation
rules. Once a native call returns, the main thread may explicitly call
`session.interrupt()` to enter the existing mandatory tail. This is allowed after
failure or lost provenance, but is attempted only once per session. Unknown
effects still require reconciliation, and cleanup still needs independent
observations before resource release. There is no implicit cleanup on a native
yield and no destructor that performs remote work.

The session and origin cannot be pickled or restored from reports. Forking or
reopening a journal does not restore the originating process's authority. A new
session encountering an already accepted correction without its original object
refuses dependent advancement. Saved request/result/validation/trace files are
useful evidence only. Current-host/manual CLI yield/resume therefore still needs
a separately qualified host-origin adapter; it cannot become direct execution
proof by importing JSON.

## Validation coverage

Focused tests replace only the unavailable native model boundary with visibly
controlled fixtures while using real Git, kubeconform, patch validation, render
export and journal acceptance. They exercise primary/replay isolation, exact
client selections, lost provenance after acceptance, tampered artifacts,
unsupported yields, cancellation, and retry refusal. These checks establish this
composition's behavior; they do not qualify the installed five-skill demo or the
complete canonical factory chain. The fixed production composition and resident `codex-cli` command now expose
these steps. A completed production phase registers its original invocation,
execution, trace, manifest and validation files through the resident session
accessor. Combined installed-package qualification and actual service/model
runs are still required before the canonical demo is accepted.

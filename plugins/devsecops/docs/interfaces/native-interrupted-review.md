# D6 interrupted read-only review

`OwnedReplayAcceptance.begin_reset` calls the fixed
`case_interruption.execute_interrupted_review(context, production=..., output=...)`
before the existing reset mutation is dispatched. The caller requires the exact
resident production coordinator and native session, the current locked reset
context, its original owned reset operation, and the actual primary fault Git
source. It prepares a new read-only review scope and a separate child RunJournal.
Neither a supplied receipt nor a saved native selection can recreate this authority.

The installed client, package, interpreter, model and reasoning selection are the
same explicitly pinned selection used by the main native session. The client
executes under the existing isolated native capability configuration. A private
process observer waits for a valid `thread.started` followed by `turn.started`.
It inspects every complete event already received; a buffered completion, failure,
result, malformed sequence or disallowed capability cannot qualify as an active
interrupted turn. The local process must still be alive when interruption begins.
The driver sends SIGINT, allows at most 0.5 seconds for graceful exit, kills the
local process group, and bounds the final reap wait to one second. An observed
client turn-start event is not a claim about provider-side dispatch or a server
turn ID.

Interruption mode cannot publish `result.json` or a successful native execution
receipt, even if late result bytes are already buffered. The separate child
journal is durably cancelled in `finally`, including preflight and execution
failures. Its cancelled request has no pending acceptance path, so later output
cannot become an accepted result. The main journal must remain at the same
projected state during this separate case, including its existing reset
operation. None of these steps advances a main native handoff.

A successful local observation issues an opaque `InterruptedReview` object.
`require_interruption(proof, journal=...)` checks the original process object,
private spawn/turn-start/alive/reap observations, original child journal, and
retained file hashes. It is historical admission only and neither renews an
expired grant nor restarts a request. It rejects copied dictionaries, unissued
objects, another journal instance, changed evidence and serialization.
`interruption_artifacts` returns at most 192 exact producer-declared files, with
16 MB per-file and 64 MB aggregate limits; it does not scan model-writable output.
The encompassing reset/finalization phase independently checks its combined
256-file and 64 MB budgets.

This proves cancellation and reaping of the observed local client process group.
Detached descendant cleanup remains explicitly unverified. Actual factory cleanup,
Git baseline restoration, the one replay, and all eight main accepted native
requests are separate checks in `replay_acceptance`; this child is never counted
among those eight requests.

The new tests use a clearly synthetic local executable for actual pipe, event,
signal, child-journal and artifact behavior, and controlled admission/scope inputs
for canonical caller tests. The selection test traverses the actual controlled
lifecycle through the original current reset. They make no provider or lab call
and do not qualify a real demo, cleanup, recording or E2E repetition.

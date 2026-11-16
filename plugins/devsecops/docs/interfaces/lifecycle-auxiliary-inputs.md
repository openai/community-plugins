# Immutable auxiliary lifecycle selections

Auxiliary inputs select exact data for the fixed owned Linux lifecycle. Selection
never grants an operation, proves a principal, creates a native capability or
establishes a successful phase. Concrete components separately require the
current journal operation, explicit action grants, original resident objects and
actual observations.

## Command surface

All selected paths are absolute and paired with exact SHA256 values. Resupply the
same selections on every resume. The six original canonical identities and the
installed plugin ID/version remain required.

```text
--operation-backend owned-linux
--mission-git-invocation /absolute/mission-invocation.json
--mission-git-invocation-sha256 <sha256>
--owned-registry-recipe /absolute/registry-recipe.json
--owned-registry-recipe-sha256 <sha256>
--owned-bootstrap-recipe /absolute/bootstrap-recipe.json
--owned-bootstrap-recipe-sha256 <sha256>
--owned-operations-recipe /absolute/operations-recipe.json
--owned-operations-recipe-sha256 <sha256>
--owned-cases-recipe /absolute/cases-recipe.json
--owned-cases-recipe-sha256 <sha256>
--driver codex-cli
--native-selection /absolute/native-selection.json
--native-selection-sha256 <sha256>
```

Legacy diagnostic selections remain supported. Full production composition
requires the bootstrap, operations, cases and native selections together before
effects. `codex-cli` retains one native session across all eight handoffs. The
`current-host` command still yields for native work; a restarted process cannot
recreate direct execution origins from saved JSON and is not claimed to have
production parity.

The mission invocation freezes its eight named configurations as before. The
bootstrap recipe's registry path/hash is independently read, validated and frozen
as `owned_registry_recipe`; a separately selected conflicting recipe is rejected.
The operations recipe's writer policy is independently read and frozen as
`owned_git_writer`. That policy contains approved role/scope and timing choices,
not guessed IDs for resources that will be created later. The actual writer
principal and credential identity come from original owned provisioning.
The bootstrap writer pin must match that same frozen policy. The operations
recipe's runtime selection is independently read and frozen as
`owned_runtime_recipe`; its target names and probe expectations are explicit
inputs, while live UIDs, addresses and Git revisions come from direct producers.

## Closed inputs and resume

The sixteen permitted frozen names are `mission_git_invocation`,
`mission_git_build`, `mission_git_staging`, `mission_git_security`,
`mission_git_signing`, `mission_git_publication`, `mission_git_proposal`,
`mission_git_commit`, `mission_git_destination`, `owned_registry_recipe`,
`native_selection`, `owned_bootstrap_recipe`, `owned_operations_recipe`,
`owned_cases_recipe`, `owned_git_writer` and `owned_runtime_recipe`. All nine mission inputs must be
selected together. Every file is a bounded regular file of at most 64,000 bytes.
Selection loading does not read credentials or contact services.

References and their exact bytes are committed atomically inside the original
`lifecycle_initialized` event. There is no later mutable binding event. An old
journal with absent selections cannot acquire them after initialization. Missing,
added, changed or mismatched input fails before dispatch. Nested paths are data
inside the explicitly resupplied pinned recipe; the CLI never restores input
paths or execution authority from old evidence.

The writer policy cannot outlive the original operator configuration. Production
build/promotion also needs explicit delivery, provision and probe grants before
CI or baseline control effects begin. Reset needs its reset, provision and probe
grants. Baseline and fault-confirmation phases only observe; fixed actors and
pre-incident controls are created in admitted mutation phases.

## Intent and evidence

Every fixed mutation rechecks the selected installation, current context and
frozen auxiliary bytes before journaling intent. The selection artifacts join
the operation's evidence, but cannot replace actual component capabilities or
action-time authorization. Unknown effects require reconciliation, never an
automatic retry.

The private pre-cleanup exporter and [final evidence publisher](terminal-evidence.md)
register all sixteen optional reference slots. These are private configuration
artifacts, not a public video bundle. A complete installed live run, the required
fresh repetitions, continuous recording and independent playback review remain
separate acceptance requirements.

The canonical native selection limits trace output to 16 MB before invocation,
matching the journal per-file limit. Completed native phase evidence must fit
the 64 MB phase aggregate. The pre-cleanup portable export retains its 60 MB
aggregate limit and the terminal bundle its 256 MB limit. These are actual
checked budgets, not guarantees that every combination of maximum-sized
component outputs will fit; required evidence is never silently omitted.

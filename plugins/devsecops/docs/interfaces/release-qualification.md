# Canonical release qualification

`scripts/qualify_release.py` checks the installed canonical path and its retained
evidence. It has no deployment implementation, alternative scenario, model
repair logic, cleanup shortcut or authority-restoration mechanism.

The checker implements the plan's G7 canonical acceptance: three successful
same-package runs, two independently fresh environments operated by a second
human, a reset/replay run, all canonical phase assertions and cleanup, eight
actual native handoffs per run, and continuous recording accepted under G7.V1.
G7 can pass when all those retained receipts and recorded human decisions pass.
Other G1–G10 gates remain incomplete until their own required receipts are
qualified; this scoped command never marks the full release qualified. G10.V1
records the separate independent viewer decision.
In particular, retained RKE2 startup is **not** a fresh-cluster receipt. The
fresh-foundation verifier requires the original terminal-bound creation intent,
exclusive new disk/generation, pristine guest observation, three authenticated
API observations, exact installed producer/package, and complete cleanup with
supplied assets preserved. Two runs must have distinct original generations,
disks, cluster/node UIDs and issued API CAs. Supplied SSH PKI may remain the same;
the producer copies and removes its owned credential files. Each fresh entry's
`environment_evidence` selects the terminal manifest's registered copies of
the creation and cleanup reports, intents, and remote observations. Journal
registration assigns numeric names, so the checker resolves document kind,
phase membership and exact bytes rather than original filenames. If transport
renewals occurred, include the registered renewal intents and authenticated
receipts. The chain must preserve the original grant, key, route and owner,
link each previous/current certificate, and show sufficient validity at the
actual bounded read-only probe. An unobserved or rejected attempt never joins
the accepted chain. Supplied source certificates are not reopened.
The native verifier also resolves each original execution manifest by phase
and hash, checks the full structured trace against its accepted result and
independent validation receipt, and matches the selected installed package,
client, requested model and effort. Temporary byte copies are read-only
validation material and never restore a native capability. An unobserved
provider model remains null. CI evidence bundles retain exact original JSON
bytes with labels, hashes and byte counts; the checker audits those bytes
without opening their historical paths or treating them as a CI capability.
Writing `environment_origin: fresh` cannot close G7. Unit fixtures and these validators do not constitute live,
installed-host, model, independent human or playable-media qualification.

## Candidate and installed runtime

Run `stage-install` with an explicit candidate ZIP, archive SHA256, content
manifest SHA256, plugin ID/version, author checkout, new staging directory,
pinned supported Codex executable/version/SHA256, and new isolated Codex home.
The command validates exact bounded archive membership, writes an outside-source
local marketplace, and emits `install-plan.json`. It does **not** install,
authenticate, provision a cluster or mark a release qualified.

The plan lists the supported local commands: client version, `plugin marketplace
add`, `plugin add devsecops@devsecops-local`, and `plugin list --json`, all using
the selected isolated `CODEX_HOME`. Review and execute them under the host's
normal authorization. Inspect the actual result before any retry. Confirm the
client banner, exact enabled plugin/version and installed cache contents.
Prepare the locked Python dependencies outside the author checkout and installed
cache using the package's frozen `uv.lock`. Configure approved native access and
explicit operation inputs separately. No source credential or existing Codex
state is copied by this workflow. Installation/authentication not performed is
an explicit outstanding prerequisite, not a successful installer test.

Invoke `run` **from the installed package**, using that package's entry point:

```text
<locked-python> -I -B <installed-root>/scripts/qualify_release.py run
  --installed-root <installed-root>
  --package-sha256 <SHA256-of-installed-SHA256SUMS>
  --plugin-id devsecops@devsecops-local --plugin-version <exact-version>
  --engine-python <absolute-locked-python>
  --author-checkout <absolute-author-checkout>
  --output <new-invocation-receipt-directory> --timeout <bounded-seconds>
  -- e2e --scenario ironbank-mission-recovery-v1 --profile bigbang-live
  --operation-backend owned-linux
  --plugin-id devsecops@devsecops-local --plugin-version <exact-version>
  --driver codex-cli --native-selection <absolute-selection>
  --native-selection-sha256 <selection-SHA256>
  --config <absolute-config> --lock <absolute-lock>
  --environment <owned-environment> --environment-inventory <absolute-inventory>
  --package-manifest <installed-root>/SHA256SUMS
  --output <new-canonical-run-directory>
  <the installed factory command's other explicit pinned operation inputs>
```

Angle-bracket values are required operator inputs, not working identities. Use
the installed factory help/schema for its complete operation input flags. The
qualifier validates with that same parser and invokes only the fixed installed
`scripts/factory_demo.py`. It verifies the actual versioned Codex cache and every
manifested file, requires a separately qualified interpreter, and runs outside
the author checkout with Python isolated imports. It records a started receipt
before spawning and retains bounded private process output and terminal outcome.
The outer timeout may leave a live effect uncertain; the qualifier does not
retry, reconcile, renew authority or infer nonexecution. Inspect the canonical
journal and supported operation status before any next action.

Native exit 3 remains a nonterminal handoff. The current-host CLI protocol still
yields exit 3, but full production current-host scope/origin restoration is not
qualified; it cannot borrow resident `codex-cli` capabilities from saved JSON.
Use the supported resident path for the continuous headless run. A process exit
0 is insufficient: the qualifier also verifies the finalized journal, result,
manifest and frozen package before recording canonical success. That records
one execution; it does not close the release gates.

## Immutable terminal and media evidence

The canonical runner publishes `e2e-result.json` and `evidence-manifest.json`
after observed cleanup and durable finalization. Verification replays the journal
read-only and checks the exact final evidence membership and bytes. It does not
repair state or invoke the lab. The result references the manifest. Neither
references media, and neither may be rewritten after recording.

Store qualification JSON, full video and companions in a separate bundle tree.
The references in qualification/video-review documents are relative to the
directory containing `qualification.json`. Run result references point into
separate run directories under that bundle. References inside `chapters.json`
are relative to the selected run directory and must name finalized evidence.
No absolute paths, traversal, symlinks or arbitrary command fields are accepted.

Use the shipped strict schemas:

- `qualification.schema.json`: exactly three distinct canonical runs on one
  package/scenario/assertion/lock identity, two fresh roles and one reset/replay,
  explicit second-operator attestations, environment evidence and video review.
- `video-chapters.schema.json`: prerequisites, all 33 canonical phases in order
  with primary/replay attempts, and terminal-result display; continuous source
  time ranges, actual observation times and exact phase receipt references.
  Five handoff mappings carry JSON pointers to actual evidence identities.
- `video-review.schema.json`: full media and companion hashes, duration, capture
  and review dates, run/result/manifest identity, exact requested client/model,
  separate completeness/playback/clarity decisions and independent reviewer
  answers. Optional edits require disclosed cuts/speeds and source time mappings.

The full recording starts before the canonical command and ends after cleanup
and displayed terminal result. Keep all waits, handoffs, real patch delivery,
reset and replay. Capture gaps, off-camera repairs or fixture replacement require
a new complete recording. There is no two-hour or short-presentation cap on the
full acceptance source. Narration or captions must explain the transitions;
provide WebVTT or SRT timecoded text. Edited presentations are optional.

An independent person who did not build the demo must watch the actual delivered
full media, check ordinary playback/readability and credential exposure, and
record concrete answers about deployment, Codex's contribution, the fault,
delivered patch, recovery/isolation and rerun steps. Metadata cannot establish
those visual facts. The checker labels their verdicts **recorded independent
review**, never machine-observed playback. Unknown provider-observed model
identity remains null; requested selection is not relabeled as observed.

```text
<locked-python> -I -B <installed-root>/scripts/qualify_release.py verify
  --qualification <bundle>/qualification.json
  --output <new-qualification-report-directory>
```

Verification checks schemas, all declared files/hashes, terminal journal replay,
same candidate identity, phase/attempt coverage, timing consistency, handoff
identity pointers, transcript timing and explicit reviewer decisions. It emits
immutable JSON, Markdown and HTML reports. Fresh receipt consistency is reported
as a specific canonical-evidence check, with the manifest's hash-only assurance.
It does not recreate a runtime capability, authenticate arbitrary JSON, or infer
hosted/disconnected interoperability or reviewer comprehension. The broader
release remains incomplete (exit 2); missing required
lanes never become skipped passes. Missing files/inputs fail closed without
executing any operation or modifying finalized run evidence.

For the dedicated runner-VM path, D6 is emitted only after the resident final
acceptance checks the original `owned-bootstrap-v2` selection, the fixed
`owned-runner-vm-v1` eleven-scope cleanup contract, and matching original runner
VM/service/routes creation and cleanup observations. The terminal manifest binds
that evidence; a copied guest identity, a metadata-only cleanup flag, the legacy
container-runner inventory or successful preboot rollback cannot replace it.
This adds no scenario phase or auxiliary input slot and does not change the
required eight main native handoffs, one reset/replay, continuous recording or
independent operator/viewer acceptance. Local controlled tests do not establish
guest execution, actual CI, live cleanup or a qualifying video.

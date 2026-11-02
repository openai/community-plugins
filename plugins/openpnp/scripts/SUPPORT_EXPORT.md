# Selective offline support export

The `support-export` launcher command exports a new owner-private, standard uncompressed USTAR archive from an owned installed state. It does not connect to OpenPnP, execute Java, load machine XML, send a message, rotate evidence or alter operational state. Its qualification covers offline export only and does not establish physical machine support.

## Launcher command

```sh
node /absolute/path/plugin/scripts/openpnp.mjs support-export \
  --state-dir /absolute/owned/installed-state \
  --output /absolute/existing-output-directory/new-support.tar \
  --operation-ids 11111111-1111-4111-8111-111111111111
```

`--operation-ids` is optional. Omit it to export summaries with no selected operation records. Multiple operation IDs use a comma-separated list with no spaces. Omit `--artifacts` to exclude every raw payload. `--state-dir` defaults to the ordinary launcher state location, including `OPENPNP_STATE_DIR` when configured. `--output` is required and must be a new absolute path.

For explicitly selected payloads, pass `--artifacts UUID:SHA256:camera` or `--artifacts UUID:SHA256:job-document`. Multiple selections are comma-separated, for example `UUID1:SHA256_1:camera,UUID2:SHA256_2:job-document`; replace each placeholder with the lowercase UUID and exact 64-character lowercase SHA256 from the observed native artifact receipt. Raw image/document bytes are copied verbatim and may contain sensitive design data. There is no selection-file or arbitrary-file option.

The command prints a JSON receipt to stdout and exits zero only on completed publication. Failures print JSON to stderr and exit one. A typed failure retains its string `code`, including `SUPPORT_SELECTION`, `SUPPORT_OUTPUT_EXISTS`, and `SUPPORT_PUBLICATION_UNCERTAIN`. Treat uncertain publication as an existing-output inspection task; do not delete it or replay native work.

## JavaScript API

```js
import { exportSupportBundle } from './support-export.mjs';

const receipt = await exportSupportBundle({
  stateDir: '/absolute/owned/installed-state',
  output: '/absolute/existing-output-directory/new-support.tar',
  operationIds: ['11111111-1111-4111-8111-111111111111'],
  artifactSelections: []
});
```

The output must not exist and must be outside operational state, including through directory aliases. The parent directory must exist, be owned by the current user and have no shared write access. No archive extraction is performed. Source leaf files and intermediate directories cannot be symbolic links; hardlinked input files are refused. Installed bridge provenance uses a fixed `bridge/<version>/` path and verifies the actual JAR hash against both installation and build receipts. Absolute paths in receipts are checked but never followed as arbitrary inputs.

The returned receipt contains `output`, `sha256`, `size`, selected operation/record/artifact counts, source structural/change flags, and explicit `native_state_modified:false`, `network_used:false`, `may_authorize_action:false`, `physical_state:'unknown'`.

Publication writes and syncs a private temporary file, exclusively links it to the new output and syncs its parent. Existing reports cannot be overwritten. If directory sync fails after publication, `SUPPORT_PUBLICATION_UNCERTAIN` leaves the output intact; inspect that file before another export. This is not a claim about host power-loss durability on every filesystem.

## Archive contents

| Entry | Scope |
| --- | --- |
| `manifest.json` | Limits, redaction/retention policy, selected artifact receipts, and exact size/SHA256 for every other entry. The outer receipt hashes the whole archive. |
| `provenance.json` | Recorded machine UUID; verified installed bridge version/upstream/hash; build-receipt hashes; exporter package/MCP/schema hashes. Runtime/bootstrap hashes are declared build-receipt values. Runtime files and the currently running bridge are not observed offline. |
| `diagnostics.json` | Bounded journal-prefix analysis, source SHA256, sequence/parse anomalies, counters, recent operation states and explicit omissions. |
| `selection.json` | Exact operation selection, record/omission counts, all recorded unresolved operation-state summaries and source completeness flags. These summaries do not replace native action reconciliation. |
| `operations.jsonl` | Every parseable line whose top-level payload names a selected operation UUID, in original source order, including stale or unknown record shapes. Each contains original line SHA256, source line/sequence, allowlisted facts and redaction counters. |
| `anomalies.jsonl` | Every detected journal anomaly, without the 100-item presentation limit of the diagnostic summary. |
| `artifacts/<uuid>.png` or `.zip` | Optional exact opaque bytes, included only by explicit selection below. |

Selected record rows are never truncated. The export fails before publication if the selection exceeds bounds. Raw selected records are **not** copied: free text, session grants, credentials, request digests, configuration, placement geometry and fields outside the schema are omitted. The redaction counter counts encountered omitted fields/subtrees, not every leaf inside an excluded subtree. User-controlled native part/board/tool/placement identifiers are replaced with SHA256 values for correlation. Such hashes are pseudonyms, not a promise of anonymization against a known list of names.

Unknown event types retain their line hash and allowlisted facts under `unrecognized-event-type`. Malformed or partial JSON cannot be attributed to an operation; the archive flags incomplete source history. A parseable selection is not proof that all historical records exist. Events without the selected operation UUID, including current unlinked manual-feed and board-load records, are not inferred to belong to the operation. Global diagnostic counts still report the scanned prefix. Selected native intents, outcomes, gaps and explicit unknown states remain visible even when a later operation state is terminal.

The journal is read only through the size present when it was opened. Concurrent appends are excluded and reported. A detected concurrent change means this export is not a stable whole-file observation. Preserve the original journal; hashes identify observations and do not establish physical effects, safe replay or integrity against an attacker who can replace all local evidence.

## Explicit raw artifact selection

```js
artifactSelections: [{
  artifact_id: '44444444-4444-4444-8444-444444444444',
  sha256: 'observed-lowercase-64-character-native-artifact-sha256',
  kind: 'camera' // or 'job-document'
}]
```

Use an already observed native artifact receipt. The exporter reads only the fixed `journal/<uuid>.metadata.json` and `.artifact` pair. ID, exact requested SHA256, length, MIME type, declared scope and PNG/ZIP signature must match. These checks bind an opaque file to its receipt; they are not full image decoding, ZIP validation or a new native document qualification.

Only native camera PNG and native job-document ZIP scopes are admitted. Configuration-backup JSON, portable-configuration ZIP, response-store files, raw journals, scripts and arbitrary external paths are unsupported selections. Unselected artifacts are never read. The exact selected image/document payload is not scrubbed or extracted and may contain sensitive image/design data. Its receipt states `content_redacted:false`.

## Limits and deferred work

- 100 distinct lowercase operation UUIDs; 16 distinct artifact UUIDs.
- Journal: 512 MiB; a record: 16 MiB; operation inventory: 20,000.
- Selected records/anomalies: 250,000 each; projected facts: 48 MiB; structure depth: 64.
- Artifact: 8 MiB; artifact metadata: 64 KiB; entire archive: 64 MiB.

This exporter does not solve runtime journal/checkpoint compaction, durable selected-evidence retention, automatic retention policy, archival consent across future sessions, secret classification for arbitrary payloads, support-bundle import or full forensic recovery. It never deletes source evidence to make space. Those remain separate runtime and product work.

## Verification scope

The retained offline API tests cover selection bounds, complete projected records, explicit malformed/unknown history, credential exclusion, input links, exact artifact bytes/hashes and archive capacity. Separate actual Node CLI-child tests cover the default empty selection, explicit operation/artifact parsing, malformed-input rejection before state reads, environment-selected state, aliased launcher entrypoints, unchanged source inventory and typed JSON failures. A test-only preload injects the final directory-sync failure after actual archive publication; the CLI preserves the archive and uncertainty code and rejects a retry against the existing output. This fault injection qualifies that software path, not arbitrary filesystem or power-loss behavior.

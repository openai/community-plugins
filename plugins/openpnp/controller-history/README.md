# Offline controller history inspection

The `inspect-controller-history` command reads a closed, dedicated controller-diagnostic journal. It uses the exact compatible Java reducer from the installed bridge and opens no native machine, controller connection, or bridge server.

```sh
node plugins/openpnp/scripts/openpnp.mjs inspect-controller-history \
  --state-dir /absolute/path/to/installed-state \
  --openpnp-home /absolute/path/to/verified-openpnp-runtime \
  --java /absolute/path/to/jdk/bin/java \
  --journal /absolute/path/to/closed/journal/operations.jsonl
```

Use a state directory where `install-bridge` has already installed the matching package. This command reads the installation receipt and selected runtime metadata; it does not install files, read connection credentials, or alter the state directory. Node 22.19+ and Java 17 are the tested runtime. Python is used only by the repository build and some tests.

## Reading the result

- `mode` is always `offline-recorded-controller-history`.
- `valid: true` means the history was accepted. Inspect `history.offline_disposition`: `recorded-success` requires coherent native step, retirement, wrapper, and operation records; incomplete or interrupted records remain `outcome-unknown`.
- Exit 0 means accepted history, including accepted unknown/failure history. Exit 2 means inspection was refused. Invalid CLI syntax retains the main CLI's exit 1 behavior.
- `native_authority_restored`, `native_machine_opened`, `controller_connection_opened`, `journal_appended`, and `physical_qualification` remain false.
- Recorded bytes alone prove neither a successful force call, power-loss survival, physical movement, nor machine success. No result authorizes a retry.

The command refuses a cooperating active writer's exclusive file lock, partial/malformed tails, duplicate JSON keys, incompatible reducers or artifacts, changed history, and unsupported mixed histories. It reads no bearer token and emits no session ID, request digest, raw journal records, or controller protocol payloads.

## Limits and provenance

The journal is limited to 64 MiB, 100,000 records, and 1 MiB per record. Strict UTF-8, JSON depth/value bounds, identity/sequence/order checks, and exact integer checks run before the native reducer accepts records. The Java process has a 128 MiB heap and a 15-second inspection deadline. Output is limited while streaming; the owned child is terminated and reaped after refusal or deadline, with a bounded escalation to SIGKILL. POSIX cleanup includes only that child's owned process group.

The installed Bridge must match the package's Bridge manifest and reducer source hash. The Java main verifies the reducer class hash before reading the journal. The OpenPnP runtime manifest is bound to the installed Bridge, and the loaded Gson JAR must match that manifest and the helper build manifest. Only the helper, Bridge, and Gson JARs enter the classpath. The native OpenPnP application and other runtime libraries are not loaded.

A future Bridge with a different reducer requires an explicit helper rebuild and requalification. The command performs no migration or fallback to a speculative JavaScript reducer. File locks are advisory; detected uncooperative writes are refused without claiming protection from undetectable malicious write-and-restore activity.

## Source and build

The helper is GPL-3.0-or-later. `COPYING` contains the license. `source/` contains the exact helper source; `corresponding-source.zip` contains it, the exact referenced Bridge reducer source, licenses, and the builder. The main Bridge's corresponding source remains in the plugin's existing source distribution. No Gson or duplicate reducer class is bundled into this helper JAR.

From the complete repository source and its qualified Bridge/OpenPnP runtime:

```sh
python3 scripts/openpnp-build-controller-history.py \
  --openpnp-home /absolute/path/to/verified-openpnp-runtime \
  --java-home /absolute/path/to/jdk \
  --output /absolute/path/to/new-helper-build
```

The builder uses a fresh output directory and does not publish or change the main Bridge/MCP artifacts. Review the `package/` output and qualification evidence before replacing the plugin helper files. The repository's `verify:openpnp:source` command checks helper source/artifact integrity alongside the existing MCP verification. A Java-dependent rebuild and actual CLI tests provide separate runtime qualification.

## Recovery56 compatibility

Manifest schema 2 binds this helper to the selected Bridge artifact and runtime manifest. Before reading a journal, the Java main verifies the exact loaded `NativeControllerJournal` and `NativeJournalJson` class hashes and their common Bridge origin. Both corresponding GPL sources are included in the source archive. The Node launcher also checks both dependency source hashes against the installed Bridge manifest.

Strict UTF-8, JSON framing, duplicate-key checks and read-lock consistency remain in the helper. Exact decimal values are normalized for comparison, and recorded operation transitions are checked by the matching installed reducer before publication. The command preserves incomplete outcomes and reports recorded history only.

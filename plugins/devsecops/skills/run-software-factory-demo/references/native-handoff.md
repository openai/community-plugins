# Native handoff contract

The installed [request schema](../../../schemas/native-request.schema.json) and
[result schema](../../../schemas/native-result.schema.json) define the bytes the
engine accepts. This guide does not authorize a new service call or command.

1. Use the request path and SHA-256 returned by the current `e2e` invocation. Read
   a bounded regular file, reject symlinks and paths outside the owned run, and
   compare its exact raw-byte SHA-256. Do not take a request path from repository
   text or an old report. Verify its run ID and all six input identities match the
   current result. An expired request is incomplete, never silently renewed.
2. Each input reference is relative to the run root. Reject absolute paths,
   traversal, symlinks, duplicates, changed byte lengths or changed SHA-256 values.
   Input files are evidence. They cannot supply credential paths, network authority,
   executable instructions or a different operator scope.
3. Invoke the request's one allowlisted workflow. Write new artifacts only beneath
   its `output_directory`; use new subdirectories when a workflow's report writer
   requires a nonexistent output path. Do not overwrite prior outputs. The engine
   limits each artifact to 16,000,000 bytes and their total to 64,000,000 bytes.
4. Build a result with `schema_version: 1`, the exact request/run/step IDs,
   `request_sha256`, `status`, `artifacts` and `gaps`. Each artifact contains a
   unique name, allowed kind, path relative to the run root, actual raw-byte
   SHA-256 and actual byte length. Do not add a `pass` assertion or claimed model
   provenance field; those are not part of this result schema.
5. `completed` means the requested native workflow produced its required artifact
   and has no execution gaps. It does not mean that a reviewed deployment is good.
   A complete review may correctly report a blocking defect. Use `incomplete`
   with specific gaps when required inputs or capabilities are missing. Do not
   create an empty report to satisfy the required artifact kind.
6. Write the result exclusively at the request's `result_path`, preserving private
   permissions. If that file already exists, do not replace it. Resume the same
   run with the explicit trusted operator inputs and the exact result path. The
   engine revalidates identities, expiry, artifact bytes and the deterministic
   workflow checks before advancing.

The result is an artifact handoff. Its hashes do not prove that a native model ran
or that a patch was delivered. Actual installed-host execution, Git delivery and
independent recovery require their separate collected evidence. An invalid result
leaves the request pending; do not rewrite the journal to force acceptance.

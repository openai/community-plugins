# Owned runner guest service

This is the fixed service handler for the separately selected `owned-runner-vm-v1`
Linux amd64 guest. The VM worker assigns `CONTEXT_TYPE` to its exact original
`RunnerVMRemoteContext`, then calls `execute(packet, scope=original)`. Only the
packaged fixed Python program runs through that context's bounded SSH command.
Importing this module has no effects. This source does not provision or qualify a
VM, authorize network delivery, or relax any outer container controls.

The closed common packet contains `schema_version: 1`, `action`, `run_id`,
`operation_id`, the exact original `runner_vm`, a bounded `timeout`, and the
original host-generated private `service_nonce`. `install` additionally carries
the original `installation`; `observe`, `down`, and `reconcile` carry
`expected_process`, retained from the original install response. This may be null
only when the original install response was unobserved. No disk receipt can
reconstruct either the host nonce or the resident supervisor's process handles.

Installation contains the registered runner ID/token/description/origin, exact
base64 input entries with hashes, selected public and private environments,
`service_trust`, `route_trust`, `guest_package`, and the fixed `guest_paths`.
`route_trust` is the original routes owner's `{files, environment,
empty_directory}` observation. Each file has its path, hash, byte size, device,
inode, UID, GID and mode. The empty directory has its path and inode/account/mode
identity. Four CA variables point to the selected private bundle, `SSL_CERT_DIR`
points to that verified empty directory, and all eight upper/lowercase HTTP,
HTTPS, ALL and NO proxy variables are empty. The service never overwrites or
removes the routes owner's files. Both the runner process and jobs receive these
fixed trust settings.

The existing `gitlab-runner` account must be UID/GID 10001 with no supplementary
groups, effective/permitted/ambient capabilities, or sudo/root management path.
The separate guest must already have NoNewPrivs=0 for its preprovisioned uidmap
helpers; the handler checks this and does not change it. The outer QEMU
container's NoNewPrivs, capability drop and network restrictions remain in force.
The root-owned GitLab Runner must report 19.5.1. The prebuilt package must match
the current run's exact manifest and qualified plugin ID/version. A bounded walk
rejects symlinks, nonregular files, missing and unmanifested members, including
old modules and bytecode. It permits at most 1024 package members, 1024
directories, 16 MB per member and 128 MB total. No tools or packages are acquired.

The private run path is `/home/gitlab-runner/.local/share/devsecops/<UUIDhex>`.
Inputs are staged beneath `inputs`; private runner configuration is under
`runner-service`. The existing full UUID output path and the 59-byte full UUIDhex
runtime path are retained. Configuration, secret values, nonce and their content
hashes are omitted from public observations. They are sent only as bounded SSH
stdin, never command arguments. Unix control messages authenticate canonical
request/response bytes plus a fresh challenge with HMAC; they never transmit the
private nonce.

A resident nonroot child subreaper owns the runner and its private cleanup
ledger. Fresh observations re-audit the supervisor and runner PID/start tick,
boot, executable bytes, UID/GID and argument identity; socket inode; actual
manager ID file; original input/configuration bytes; and route trust. Cleanup
enrolls descendants only through this original supervisor's current kernel child
edges across all threads. It pins each process with a pidfd, validates ancestry,
freezes parents before discovery, rechecks the deadline immediately before
signals, and sends signals only through those original pidfds. Every signal
handle requires all UID/GID values to remain 10001; mapped or privileged
descendants remain residual for the original VM disposal path. Executable observation reads `/proc/<pid>/exe` itself, supporting visible
mount namespace paths without resolving them against the observer's root.
There is no global process scan, process-group kill, PID reuse fallback, or
saved JSON process adoption.

Complete service cleanup requires affirmative child reaping and ECHILD, removal
of the service's exact inode-bound staged files/directories, preserved route
trust, and client-observed termination of the original supervisor plus removal
of its socket. Build outputs and caches remain on the separately owned disposable
VM disk for that owner's later removal. Unknown file effects, inaccessible or
unreapable descendants, a lost supervisor, or a lost cleanup reply remain
residual. The helper never retries a previously attempted down operation.
Read-only reconciliation authenticates the original operation and reports
retained state. Only the separate reviewed original VM kernel-cessation proof,
combined with actual registration revocation and the parent owner's required
checks, can close an inaccessible guest; it does not resolve CI effects.

Local tests exercise the packaged validator, canonical VM caller, resident
control protocol, partial writes, PID ancestry refusal, private-data exclusion,
trust/package drift, and cleanup refusal. One test reaps an actual local child
with controlled Linux adapters; another invokes the packaged program locally and
verifies pre-effect rejection. These are source/component checks. They do not
qualify real Linux subreaper/pidfd behavior, the selected VM image, GitLab runner
registration, a live CI build, or an end-to-end recording.

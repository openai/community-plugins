# Local Git transport preparation

This interface prepares bounded local Git inputs for a later delivery adapter.
It does not contact a Git service, select a remote destination, send a pack,
create a branch or establish canonical demo completion.

Source checkpoint: `f7ce2ae2bb09b9d3e031780bd5ffd16db7386ee6`. This is a
new adapter contract; the controlled transport experiments do not themselves
supply production authority.

## API and admission

```python
report, transport = prepare_git_transport(
    prepared=prepared_commit,
    run_scope=run_scope,
    source_commit=source_commit,
    deployment_commit=base_commit,
    proposal_configuration_sha256=proposal_configuration_sha256,
    configuration_sha256=commit_configuration_sha256,
    output=absolute_new_directory,
    timeout=120,
)
```

All parameters are keyword-only. `prepared` must be the direct, current
`PreparedDeploymentCommit` issued by existing preparation. Admission must use
its existing capability validator and preserve its origin and input checks.
A supplied report, reconstructed object, saved JSON or controlled-test source
cannot issue a production transport plan.

The explicit run scope, source commit, selected deployment base and both
configuration pins must agree with that capability. Here
`configuration_sha256` is the existing deployment-commit configuration pin;
it is not a remote Git configuration. The prepared new commit, its sole base
parent, tree, object records and exact change remain bound to the admitted
preparation. The inherited image registry origin and repository retain their
existing meanings. They are never interpreted as a Git origin, project or
repository path.

`output` must be an absolute, new private directory outside the source
repository and the retained prepared evidence, admitted with the existing path
and repository checks. Preparation uses one finite deadline from 1 to 120 seconds, covering admission,
native subprocesses, local validation and retained output. Recheck frozen
inputs and output identity before publishing the result; changed, incomplete
or unsupported inputs fail closed.

A successful result returns a report and a direct `PreparedGitTransport`.
This object is nonserializable and bound to the issuing process, run, admitted
inputs and expiry. Each later use must revalidate those bindings and retained
bytes. Its authority is local preparation only: it grants no network access,
remote write, object omission or delivery/recovery authority. Failures and
controlled execution return no production transport capability. Invalid inputs
or interrupted audits raise without a passing terminal receipt. A fully checked
controlled input returns an unknown report and no plan.

Python artifact writes are anchored to held directory descriptors. A fixed
isolated Python child inherits explicit descriptors, calls `os.fchdir`, checks
the pinned Git executable and uses `execve`; native output paths are fixed
relative names under held private directories. Standard input/output files are
already open and unlinked beneath held scratch. There is no threaded
`preexec_fn`, `/dev/fd` dependency, shell or change to the shared process runner.
Unsupported runtime/descriptor inputs fail closed. Runtime qualification must
record the actual host and Python version; one host's checks do not qualify all
POSIX deployments.

The fixed Python shim remains the live session leader. It forks the exact
pinned target in its own group, collects that target's status, writes one
four-byte result over a private pipe, and waits for group cancellation. The
target closes both control descriptors before executing. Completion detection
does not poll or reap the leader. The parent stops the owned group first and
reaps the leader once; the returned tool status is the exact target status.
Cleanup is idempotent, including exceptions after output collection, so a
second cleanup attempt cannot signal a reused numeric process-group ID.

This uses standard-library POSIX fork, waitpid and private pipes on supported
Python versions. A separate owner pipe makes the supervisor stop its own group
if the caller disappears, both while the target runs and after its completion.
The caller must keep the default SIGCHLD disposition and exclusively own its
supervisor's wait status. Detected external reaping rejects cleanup without
signalling that old ID. A group-permission failure cannot establish descendant
cleanup. The supervisor remains live because [macOS excludes zombies from group
signalling](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_sig.c)
and can return EPERM for an otherwise empty zombie-only group.

The approved native tool, Python runtime and private scratch contents retain
the existing trusted-host integrity assumption. As in deployment-commit
preparation, executable identity is checked before and after each native call;
the child additionally hashes its held executable descriptor and matches the
selected pathname immediately before execution. Final `execve` remains
pathname-based. This is not atomic descriptor execution and does not promise
resistance to hostile same-user replacement between that final check and exec.
Working-directory selection and generated artifact/stdio writes are separately
anchored to held descriptors, so changing source/output ancestor paths cannot
redirect those operations.

## Complete local base and retained evidence

Prepare a private native Git object store containing the selected base commit
and its complete reachable tree/blob closure. Do not traverse or import base
ancestor history. Symlinks are opaque blob bytes; do not follow their targets.
Reject unsupported object modes and repository layouts. Do not fetch missing
objects, use alternates, introduce shallow markers, alter source metadata or
write into the source repository.

The complete selected-base closure has these independent maximums:

| Bound | Maximum |
| --- | ---: |
| Unique objects | 256 |
| Total raw object bytes | 8 MiB |
| Raw bytes per object | 2 MiB |
| Tree depth | 16 |
| Expanded tree entries, including repeated paths | 20,000 |

The selected base commit is limited to 64,000 bytes. This first local transport
slice accepts at most 18 prepared objects and an 8 MiB prepared pack; it rejects
larger inputs even where earlier preparation allowed a 9 MiB pack. Base pack
output is bounded to 9 MiB and aggregate native output to 32 MiB. No limit is
silently enlarged to accommodate an input.

Retain a native-generated `base.pack` and its native index, plus the original
prepared pack/index and exact prepared commit/configuration bytes. Record
their sizes and SHA-256 hashes. Verify Git object IDs, kinds, raw lengths and
SHA-256 records against the exact base and prepared inventory. Preserve
existing stricter preparation bounds and checks; the closure limits do not
expand them. Retained byte identities must be checked before issuing or
reusing the plan.

The base pack and original prepared pack are distinct artifacts. A transmitted
pack and request have not been generated: their identities remain absent.
Local base membership does not prove remote possession, so omission of reused
objects remains forbidden. Neither the experiment's `ReuseBinding` nor a
matching advertisement can authorize omission.

## Local consumer output admission

An in-process consumer can retain local evidence without gaining network or
delivery authority:

```python
with open_transport_consumer_output(
    transport,
    run_scope=run_scope,
    source_commit=source_commit,
    deployment_commit=base_commit,
    proposal_configuration_sha256=proposal_configuration_sha256,
    configuration_sha256=commit_configuration_sha256,
    output=absolute_new_directory,
    deadline=shared_monotonic_deadline,
) as admitted:
    admitted.audit()
    reference = admitted.write_bytes('observation.bin', raw_bytes)
    admitted.write_bytes('report.json', canonical_report_bytes, terminal=True)
```

The issuer retains the original repository and `.git` identities privately when
creating the transport plan. Admission requires that live direct plan and its
exact five-field scope. The consumer output must be new and outside the original
repository, preparation evidence and transport evidence. Existing ancestors are
opened without following symlinks and compared by filesystem identity before
any directory creation. New directories are created relative to the admitted
held parent, with route and protected-root audits between creations. Caller
scope is copied, and all source capabilities, identities and retained bytes are
rechecked before and after writes and on normal context exit.

The yielded handle is nonserializable, process-bound and valid only within its
context. `write_bytes` accepts exact `bytes`, a single basename matching
`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`, and an optional Boolean `terminal`. Writes
are exclusive, private, and relative to a private held directory descriptor.
Limits are 8 MiB per file, 16 MiB total, and 64 files. It returns the retained
SHA-256 hash and byte count. Unissued files, altered retained bytes and changed
routes fail subsequent audits. `directory_fd` is a separate borrowed descriptor
for bounded reads only: do not close, retarget or write through it. Tampering
with it fails admission to later writes; cleanup uses the private descriptor.
An exceptional exit or failed write/audit withdraws still-identical terminal
files owned by this context while retaining nonterminal diagnostic artifacts.
Consumers must finish their report inside this context and accept it only
after successful context exit.

The caller supplies one finite future monotonic deadline, at most 120 seconds
away, covering admission, all writes and exit audits. The existing validator
also accepts `deadline=`; it clamps each audit to the earlier of that deadline
and its original ten-second budget. Omitting it preserves the original budget.
Consumers must pass their shared deadline to every operation rather than renew
the budget for each stage.

Descriptor anchoring prevents a substituted pathname or symlink from redirecting
writes into another directory. The existing trusted-host and private-directory
integrity assumptions still apply: portable POSIX operations cannot atomically
freeze filesystem topology against a hostile same-user actor moving a held
directory between checks. Observed topology changes fail the operation and
withdraw owned terminal evidence; failed attempts can retain directories and
diagnostics under the originally held destination. This interface does not
claim atomic topology or remote possession, and does not implement an observer,
credential handling, HTTP, a Git write or delivery/reconciliation authority.

## Separate remote delivery contract

A later adapter must independently select and authenticate the exact Git
origin, project ID, repository path, base ref/base commit and create-only
destination ref. It must establish remote possession through bounded scoped
evidence, preserve current capability/input/output admission, and validate
the actual native-generated transmitted bytes and request.

Before a write it must retain and revalidate durable mutation intent. After
a write it must independently read back the exact ref, commit/sole parent,
tree and intended change. A missing or ambiguous response requires scoped
reconciliation, not automatic retry, overwrite or deletion. Exact-name server
behavior, including symbolic or dangling-ref redirection and service hooks,
requires its own qualification. No local preparation receipt closes those
contracts or CI, MR, deployment, recovery, repeatability or recording gates.

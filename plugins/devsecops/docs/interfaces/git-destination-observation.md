# Git destination observation

`devsecops.factory.git_destination.observe_git_destination(...)` observes one
explicitly selected GitLab project and Git smart-HTTP destination. It accepts a
current, direct `PreparedGitTransport` and the same five scope fields used by
that issuer. It returns a sealed report, never a capability for delivery or
object omission. No CLI or lifecycle handler is enabled by this component.

```python
report = observe_git_destination(
    transport=current_direct_transport,
    run_scope=run_scope,
    source_commit=source_commit,
    deployment_commit=selected_base,
    proposal_configuration_sha256=proposal_configuration_sha256,
    configuration_sha256=commit_preparation_configuration_sha256,
    config=destination_configuration_path,
    expected_config_sha256=independently_selected_destination_config_sha256,
    output=new_absolute_output,
    timeout=60,
)
```

The API validates the process-bound, expiring transport object before reading
configuration. Saved JSON, reconstructed instances, wrong scope and expired
objects cannot reach credentials or requests. The issuer-owned output context
protects the deployment repository, original preparation and transport evidence,
then performs all writes through held descriptors. The consumer uses only the
context's exclusive writer and audit methods. Context failure withdraws a
terminal report created by that context; partial raw evidence remains explicit.

## Closed selection

The independently pinned JSON configuration has exactly these fields:

```json
{
  "schema_version": 1,
  "kind": "mission-git-destination-observation-configuration",
  "origin": "https://git.example.test",
  "project_id": 37,
  "repository_path": "/group/deploy.git",
  "base_ref": "refs/heads/main",
  "base_commit": "1111111111111111111111111111111111111111",
  "target_ref": "refs/heads/demo-review",
  "path_with_namespace": "group/deploy",
  "api_read_credential": {"kind": "bearer", "token_env": "GITLAB_API_READ"},
  "git_read_credential": {
    "kind": "basic",
    "username_env": "GITLAB_GIT_USER",
    "password_env": "GITLAB_GIT_READ"
  },
  "trust": {"mode": "system"}
}
```

These values are illustrative, not an issued service grant. The selected base
must match the direct local transport binding. The target must be a distinct
branch. Git destination fields remain separate from the original image registry
origin/repository and from the newly prepared deployment commit.

`path_with_namespace` may be null when the caller selects only immutable numeric
project identity and exact repository URL. If supplied, it must match the literal
repository path and the API response. `git_read_credential` may instead be an
explicit bearer reference. API and Git environment names must be disjoint. Each
selected header is captured once in memory, used only by its own client, and
cleared when collection ends. No token, password or authorization-header hash is
retained. There is no ambient authentication fallback or challenge exchange.

`trust` may instead have exactly `mode: "bundle"`, an absolute `path`, and its
independently selected `sha256`. The bounded ASCII PEM bytes are frozen privately
and loaded directly into a verifying TLS client context through `cadata`; TLS
setup never reopens the retained output pathname. TLS hostname verification,
required certificates, TLS 1.2 minimum, exact-origin requests, disabled redirects
and the existing transport's proxy behavior remain intact.

Origins must be canonical literal HTTPS origins: no user information, path,
query, fragment, trailing slash, explicit default port, noncanonical address or
normalization. Repository paths are literal slash-separated ASCII components
ending in `.git`; percent-encoded paths and route ambiguity are unsupported.
Ref names use a bounded ASCII subset of Git's syntax. Unsupported inputs are
rejected rather than normalized into another selection.

## Fixed four-read protocol

The adapter performs this sequence exactly once:

1. GET `/api/v4/projects/{numeric_project_id}` with the API read header. Require
   the exact integer ID and exact selected `http_url_to_repo`; compare the
   selected namespace if supplied. Returned URLs never choose another request.
2. GET `{repository_path}/info/refs?service=git-upload-pack` with the separate Git
   read header. Require the exact upload-pack advertisement media type.
3. Repeat both reads. Compare selected project identity and the complete parsed
   ref/capability advertisement with the first observations.

The protocol follows the public [Git smart HTTP specification](https://git-scm.com/docs/http-protocol)
and [GitLab project API](https://docs.gitlab.com/api/projects/). It implements a
closed v0 advertisement subset: exact service prelude and flush, lowercase
four-hex packet lengths, one first-ref NUL capability section, ordinary SHA-1
refs, optional `HEAD`, validated tags and corresponding peeled tag entries, and
one final flush with no trailing bytes. Selected base identity must match.
Duplicates, truncation, special delimiters, unsolicited version negotiation,
zero or non-SHA-1 object IDs, unknown capabilities and unsupported ref forms are
rejected. There is no dumb-protocol fallback, fetch, POST or receive-pack call.

Supported capabilities are explicit parser allowlists, including common v0
upload-pack flags, `object-format=sha1`, a bounded agent string and an optional
`symref=HEAD:refs/heads/...` that agrees with the advertised HEAD/branch IDs.
Capability advertisements do not authorize their use by this component.

Each API response is at most 64 KiB; each Git response is at most 2 MiB and
10,000 advertised entries. Each client permits at most two requests. Aggregate
response bodies are bounded by `2 × (64 KiB + 2 MiB)`. Both clients and local
admission audits share the selected monotonic deadline of at most 60 seconds;
each network request additionally has a limit of at most 30 seconds. No
pagination or automatic retry occurs. Expiry detected in an audit prevents the
next request or terminal publication; this is not an OS-level kill timer for
arbitrary filesystem or TLS-context initialization stalls.

## Evidence and limits

The private output contains the frozen selection, optional CA, bounded raw
responses, sanitized transport receipts and `git-destination.json`. The report
links response hashes, request sequence and observation times. Every source
input, direct transport binding and retained artifact is rechecked before and
after requests and terminal publication. Input, output, capability or deadline
changes leave no terminal success. Failures that preserve local admission may
produce a sealed partial report with a safe error category.

A complete observation has `decision: "pass"` only for these reads. Every report
retains `factory_decision: "unknown"`, `remote_possession: "unverified"`,
`omission_authorized: false` and `delivery_authority: "not-issued"`. Repeated
responses are not an atomic snapshot. Target absence means only absence from
the observed advertisements; it is not create-only permission, proof of hidden
ref absence or evidence that any object may be omitted from a future transfer.

Remote object possession, Git mutation intent, create-only behavior, delivery,
independent readback and unknown-write reconciliation are separate future
boundaries. Scripted offline tests do not qualify a live server, TLS transport,
issued credentials, Linux/native-client runtime or the canonical full demo.

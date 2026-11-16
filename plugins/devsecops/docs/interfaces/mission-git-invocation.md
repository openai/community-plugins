# Explicit mission Git preparation invocation

`scripts/publish_and_prepare_mission_git.py` is a fixed caller of
`qualify_publish_and_prepare_git`. It can acquire/build the selected mission base
and publish image evidence to the selected registry. Its Git stages create local
commit/transport objects and perform bounded destination/base GETs only. They do
not push, create a merge request, merge, sync Argo or deploy. Existing upstream
intent/evidence is retained when a later stage fails; this caller never retries.

## Independently selected inputs

Supply `--invocation /absolute/private/invocation.json` and
`--invocation-sha256 <independently-selected-sha256>`, plus mandatory
`--expected-source-repository /absolute/source` and
`--expected-source-revision <immutable-commit>`. Source arguments must equal the
envelope and build configuration. A saved operation result is not an input.
The approved external interpreter can invoke the installed script with `-I -B`;
the script selects that installation's `src` explicitly. Isolated Python mode
ignores inherited Python import settings; it does not authenticate the host.

The closed `schemas/mission-git-invocation.schema.json` contains:

- `schema_version: 1`, `kind: mission-git-preparation-invocation`.
- `configurations`: exactly `build`, `staging`, `security`, `signing`,
  `publication`, `proposal`, `commit`, `destination`; each contains an absolute
  `path` and SHA-256. Each configuration keeps its existing component contract.
- `run_scope`: the six independently selected scenario, assertions, package,
  lock, factory-config and environment SHA-256 inputs.
- `plugin_id`, `plugin_version`: selected installed package identity.
- `source`: absolute `repository` and immutable `revision`.
- `output`, `runtime_root`: distinct new owned private roots outside source,
  deployment and installed package trees. The composition enforces exclusions.
- `timeout`: overall seconds, 249 through 3960 inclusive.

The shared read-only `mission_git_invocation.invocation_configuration(raw)`
validates at most 64,000 bytes, closed fields, exact versions, hashes and literal
absolute paths without parent traversal. `load_invocation(path, expected_sha256)`
adds bounded no-symlink file reading and exact-byte pin validation. Neither
function executes a stage or grants a capability. The effectful CLI derives
arguments from those current bytes once, then invokes the composition once.
The composition freezes all eight selected configs outside checkout before effects.

The envelope is separate from factory-config to avoid a hash cycle: build config
already contains the factory-config hash in its run scope. Envelope hashes and
six input identities do not identify a unique journal run, authenticated CI job,
runner, credential principal, service grant or mutation intent. A lifecycle
caller must independently bind fresh inputs and current intent before effects;
this change does not register a lifecycle handler or complete preflight.

## Diagnostic output and CI recipe

Stdout contains one bounded allowlisted JSON result: fixed kind/version/scope
markers, decision, four stage enums and the sealed operation hash when available.
It excludes paths, raw errors, child reports, configs, private artifacts and all
direct handles. Exit 0 means this narrow component passed, 1 preserves an explicit
policy failure, and 2 means unknown/incomplete or rejected inputs. Argument errors
exit 2 with fixed stderr text. Interruptions propagate. Do not retry from a summary;
inspect retained private intent/evidence and reconcile any uncertain effect first.
The CLI discards direct handles on exit; no artifact restores them. A successful
summary still says factory unknown, CI/lifecycle not established, Git write not
performed and delivery authority not issued.

Select `demo/software-factory/ci/gitlab-git-preparation.yml` **instead of** the
legacy proposal recipe. Including both would repeat upstream publication. The
alternative includes direct MR-only job rules, matching the detached
`merge_request_event` pipeline required by the MR component. Supply an authorized owned Linux shell runner,
approved external interpreter, verified installed plugin, independently selected
envelope/pin, private external inputs/output/runtime and existing credential
references. No automatic dependency or credential acquisition is configured.
`CI_PROJECT_DIR` and `CI_COMMIT_SHA` are declarations checked against the envelope;
authenticated job/runner/source evidence must be collected separately.

[GitLab's artifacts paths](https://docs.gitlab.com/ci/yaml/#artifactspaths) are
relative to the project directory and cannot directly link outside it. The recipe
therefore captures only stdout to `mission-git-summary.json` in the checkout. It
rejects an existing path/symlink, sets private permissions and shell noclobber,
and leaves the invocation as the final subshell command so its exit is preserved.
This requires the selected checkout to be owned by the runner during capture;
it is not a defense against another process mutating that directory concurrently.
`retry: 0` prevents job-level automatic repetition. Only that one summary is
archived on job success. Failure/unknown diagnostics remain in the external
private evidence tree: failure artifacts are disabled so a preexisting
candidate-supplied file cannot be uploaded after the refusal guard. A timeout or
interruption may leave no complete JSON; never treat a partial file as success. The private
execution tree and eight frozen configs remain external and unarchived.

## Verification boundary

Controlled tests exercise the parser, actual CLI entry, isolated-mode extraction,
recipe exit/no-clobber behavior, package membership, input drift and real native
local Git stages with fixture upstream issuance/GETs. They do not establish live
registry, installed release, authenticated CI, remote Git delivery, Argo/RKE2,
canonical repetitions or recording readiness. Operator publication authorization
and the existing acquisition restrictions still apply to any actual invocation.

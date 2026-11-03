# Loupe Code Review

Loupe is the `la-review` Codex plugin. Invoke `$la-review:loupe` to run independent specialist reviewers, verify their candidate findings, and consolidate the evidence for a selected Git scope.

Loupe requires stable Codex CLI 0.137.0+, Python 3.6+, Bash, Git, `jq`, and at least one authenticated `codex` or `claude` reviewer executable. It supports Ubuntu 18.04+ and macOS 14+; native Windows and WSL are not supported.

## Data and permissions

Loupe has no hosted service, plugin-owned account, telemetry collector, or external data store. The active Codex session resolves the requested Git scope and saves a verification diff in a private temporary directory. The bundled Python runner then launches the locally installed `codex` and, when available, `claude` CLIs. Each child reviewer can inspect repository files and command output beyond that diff; material it reads may enter the context sent to the user's configured OpenAI or Anthropic provider. Authentication, quota, and provider-side retention follow the user's CLI and provider configuration. Review only material that the user is authorized to send to those providers.

The child Codex reviewers use a `workspace-write` sandbox, and Claude uses automatic permission mode. Loupe instructs them to review without editing source files, staging, committing, or installing dependencies, while allowing local validation and manual tests. That instruction is a behavioral contract, not a filesystem barrier: tests and reviewer tools may create caches or temporary files, and a child process has the permissions of its configured mode. The outer runner also needs access to the CLIs' normal user-level state under locations such as `~/.codex/` and `~/.claude/` for authentication, configuration, and session operation. No plugin-owned credential store is created.

## Local artifacts and retention

The active session keeps `review.diff` and `reviewers.json` in a private temporary directory. The JSON contains each reviewer's full captured output, errors, status, and any session metadata; the runner also prints that JSON to the active session. The runner's intermediate output files are closed after execution. Claude records a persistent session transcript in its normal configuration tree, which Loupe may read to recover truncated findings. Codex is invoked with `--ephemeral`, but both CLIs may still write their own normal runtime files. Loupe does not control CLI or provider retention, or copies of output retained by the active Codex session.

After a completed review with no timeout or unexpected blocker, the skill deletes its two named artifacts and removes the now-empty directory. On a timeout, malformed result, verification blocker, or other unexpected issue, it keeps the directory and reports its path for diagnosis. CLI session data is governed by each CLI's own settings and is not removed by this cleanup.

- [Complete Loupe guide](https://github.com/pallgeuer/la-dev-codex-plugins/blob/main/docs/loupe.md)
- [Installation](https://github.com/pallgeuer/la-dev-codex-plugins/blob/main/docs/installation.md)
- [Privacy](https://github.com/pallgeuer/la-dev-codex-plugins/blob/main/PRIVACY.md)
- [Terms](https://github.com/pallgeuer/la-dev-codex-plugins/blob/main/TERMS.md)
- [Security](https://github.com/pallgeuer/la-dev-codex-plugins/blob/main/SECURITY.md)
- [Support](https://github.com/pallgeuer/la-dev-codex-plugins/issues)

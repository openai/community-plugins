# AGENTS.md Auditor tests

The suite is synthetic, offline, and deterministic. It needs no credentials,
network access, or development dependencies; the plugin itself has none.

Run from the repository root:

```sh
npm run test:agents-md-auditor
```

That command is the plugin's master suite and the one the marketplace runner
calls. CI runs it in a single `AGENTS.md Auditor E2E` job on Node 22.19, 24,
and 26 in sequence, after a cold copied-package check.

Tests are grouped by execution boundary:

- `unit/` checks manifest, catalog, and skill metadata, README coverage of every
  rule, and the command, Makefile, and Markdown parsers;
- `integration/` runs the CLI against the committed `healthy`, `stale`, and
  `monorepo` fixtures and against generated temporary repositories (oversize
  chains, path heuristics, `cd` handling, discovery limits, exit codes), verifies
  the audited tree is unchanged, and runs a copied package from an unrelated
  working directory;
- `security/` checks the distribution for symlinks, caches, personal paths, and
  secret-shaped values; confirms the checker source has no network, spawn, or
  write APIs; and verifies redaction, symlink non-following, outside-root
  containment, and that reports contain no absolute paths.

Credential-shaped and oversize inputs are generated in temporary directories at
test time, so no such values are committed. The symlink test is skipped, and
reported as skipped, on hosts where symlinks cannot be created; a skip is not a
pass.

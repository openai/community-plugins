# Synthetic fixtures

Owned, neutral sample repositories for `agents-md-auditor`. They contain no
customer data, credentials, private URLs, or copied third-party source.

- `healthy/`: an AGENTS.md whose paths, scripts, and make targets all exist.
  Expected result: no errors and no warnings.
- `stale/`: broken paths, an undefined pnpm script, an undefined make target,
  a missing link, a package-manager mismatch, a duplicate heading, and a vague
  instruction.
- `monorepo/`: nested files, an instruction repeated from the parent, an
  override that shadows AGENTS.md, a script that exists only at the root, and
  a `cd` into a sibling package.

Oversize and credential-shaped cases are generated in temporary directories by
the test suite so that no secret-shaped value is ever committed.

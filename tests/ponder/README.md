# Ponder tests

The Ponder test suite is synthetic, offline, and deterministic. It validates the multi-pass deliberation ledger, coverage gate, and four-perspective deliberation checks without requiring credentials, network access, or external packages.

Run from the repository root:

```sh
npm run test:ponder
```

Tests are grouped by execution boundary:

- `unit/` checks manifest, marketplace catalog, skill frontmatter, `openai.yaml` metadata, and public README contracts;
- `integration/` exercises read-only synthetic fixture inspection, full two-pass convergence (`init` -> `enumerate` -> `pass 1` -> `pass 2` -> `verify` -> `report`), fail-closed incomplete coverage and missing-voice gates, and copied-package execution outside the repository;
- `security/` verifies symlink rejection, path-traversal blocking, read-only fixture invariants, and absence of private paths, credentials, caches, or `node_modules`.

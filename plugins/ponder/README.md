# Ponder

Run a coverage-gated, multi-pass review and deliberation loop over an explicitly declared codebase, UI, or plan with an append-only delta ledger.

Version 0.1 starts with a read-only inspection flow on the bundled synthetic fixture, requires an explicitly declared source scope and session directory before any ledger writes, evaluates four explicit perspectives (`skeptic`, `builder`, `dreamer`, `empath`), and refuses to stop until every enumerated unit is graded and the delta ledger converges.

## Safe first run

```text
Use $ponder on the bundled synthetic fixture only to inspect unit coverage, pass convergence, and ledger integrity. Do not edit files yet.
```

The safe first prompt inspects only `fixtures/synthetic/sample-audit` and reports unit coverage, deliberation-gate completeness, pass-over-pass delta convergence, and ledger integrity with `readOnly: true` and `writesPerformed: false`.

## Workflow

1. **Read-only inspection (`inspect`, `coverage`, `verify`)**: Inspect an existing session or synthetic fixture without modifying any files.
2. **Session initialization (`init`)**: Initialize an append-only ledger inside an explicitly declared `--session-dir` with a goal, lens (`code`, `ui`, `architecture`, `tests`, `docs`, `copy`, `product`), and perspective set.
3. **Exhaustive enumeration (`enumerate`)**: Enumerate the concrete units inside the declared source scope (`units.json`) before grading begins.
4. **Multi-pass deliberation (`pass`)**: On each pass, record the four-perspective deliberation (`deliberation-N.json`: `skeptic`, `builder`, `dreamer`, `empath`) and append delta lines (`deltas-N.jsonl`: `add`, `change`, `retract`, `keep`, `unit-add`). The CLI folds deltas into `findings.json`.
5. **Coverage and convergence gate (`verify` / `report`)**: Verify that 100% of enumerated units have at least one live finding or explicit grade, every active voice has contributed on every pass, and the latest pass has zero `add`, `change`, `retract`, or `unit-add` deltas (`keep` only) before emitting the final prioritized report.

The bundled dependency-free helper, `scripts/ponder.mjs`, enforces these boundaries offline with Node.js built-ins only. Its `inspect`, `coverage`, and `verify` commands are strictly read-only (`readOnly: true`, `writesPerformed: false`), while `init`, `enumerate`, `pass`, and `report` write ledger files only inside the explicitly approved `--session-dir` and reject any path traversal or symbolic link.

## Required inputs and stop conditions

Provide:

- the exact source files, UI routes, or plan documents allowed for inspection;
- one explicit target session directory (`--session-dir`) when recording a new deliberation ledger;
- the review goal and lens (`code`, `ui`, `architecture`, `tests`, `docs`, `copy`, or `product`);
- whether the run is read-only inspection or an approved ledger session.

The skill stops before writing when the source scope or target session directory is missing or ambiguous. It also stops rather than editing application source files during a review pass, writing outside the declared session directory, or claiming convergence while ungraded units or non-`keep` deltas remain.

## Permissions and authentication

Capabilities: `Read` and `Write`. This plugin has no MCP server, app dependency, external account, network call, token, or credential flow. Its marketplace authentication policy is `ON_USE`, and using it requires no sign-in or API key. It needs read access to the explicitly declared source scope and write access only to the explicitly declared session directory when recording ledger passes. The fixture inspection, coverage, and verification flows are read-only.

## Data boundary

Do not paste secrets, customer data, private repository URLs, or credentials into a prompt or ledger file. The plugin runs completely offline and does not transmit code or findings over the network. Ledger writes are restricted to the explicitly declared session directory, and review passes never overwrite application source files.

## Accessibility

When reviewing UI surfaces (`--lens ui`) or generating markdown reports, Ponder checks semantic structure, keyboard navigation order, focus visibility, screen-reader labels (such as VoiceOver and ARIA semantics), Dynamic Type and zoom behavior, contrast, and reduced-motion states. Generated reports use plain markdown headings and tables without ASCII-only diagrams that break screen readers.

## Limitations

- v0.1 is a structured review and deliberation ledger; it does not automatically rewrite application source files or guarantee defect-free software.
- Convergence proves that every enumerated unit in the declared scope was graded across the configured passes and perspectives; it does not substitute for running your test suite, typechecker, or human review.
- It operates only on local files in the declared scope and does not fetch remote pull requests or issue trackers.

## Install and verify

From a reviewed checkout of this marketplace:

```sh
codex plugin marketplace add .
codex plugin add ponder@community-plugins
```

After installation, start a new Codex task and use the safe first prompt above. Contributors can validate this package from the marketplace root:

```sh
npm run validate:ponder
npm run test:ponder
```

No network connection, external CLI install, or API key is required.

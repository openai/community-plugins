---
name: ponder
description: Run a coverage-gated, multi-pass review and deliberation loop over an explicitly declared codebase, UI, or plan with an append-only delta ledger.
---

# Ponder

Run repeated grading passes over an explicitly declared source scope until the finding ledger stops changing. Each pass evaluates the subject through four explicit perspectives (`skeptic`, `builder`, `dreamer`, `empath`), records structured arguments in `deliberation-N.json`, appends delta operations in `deltas-N.jsonl`, and enforces 100% unit coverage before allowing a converged stop.

## Safety contract

- Start read-only when inspecting existing sessions or the bundled synthetic fixture (`scripts/ponder.mjs inspect`, `coverage`, or `verify`). Do not edit files during read-only inspection.
- Never edit, overwrite, format, rename, or delete application source files in the declared source scope during a review pass.
- When recording a new deliberation session (`init`, `enumerate`, `pass`, `report`), write ledger files only inside the explicitly declared `--session-dir`.
- Reject symbolic links, parent-directory traversal (`..`), and any path outside the declared `--session-dir`.
- Do not copy secrets, credentials, customer data, or private repository URLs into ledger artifacts.
- Do not claim convergence when any enumerated unit is ungraded (`coverage < 1.0`), when any configured voice is missing from `deliberation-N.json`, or when the final pass still emits `add`, `change`, `retract`, or `unit-add` deltas.

## Required declaration

Before starting a review or ledger session, require all of:

- an explicit source scope: the concrete files, directories, UI routes, or plan sections allowed for inspection;
- the execution mode: read-only inspection of an existing session/fixture, or a bounded ledger run in an explicitly declared `--session-dir`;
- the review goal and lens (`code`, `ui`, `architecture`, `tests`, `docs`, `copy`, or `product`);
- the minimum pass count (default `2`, because a single pass cannot measure revision convergence).

If the source scope or target `--session-dir` (for ledger writes) is missing or ambiguous, stop and ask for the missing input before writing any files.

## Four perspectives

Every pass must record an explicit, non-empty argument from each active perspective in `deliberation-N.json`:

1. **Skeptic (`skeptic`)**: Demands concrete file-and-line evidence for every finding. Retracts (`retract`) speculative, duplicate, or unsupported claims and verifies that severity ratings match actual failure impact.
2. **Builder (`builder`)**: Checks whether each finding is actionable and minimal. Refines (`change`) vague recommendations into concrete file-level remedies and verifies architectural boundaries.
3. **Dreamer (`dreamer`)**: Searches across units for systemic patterns, missing edge cases, ungraded units (`unit-add`), and failure modes that single-file inspection missed (`add`).
4. **Empath (`empath`)**: Evaluates the human impact on end users and maintainers, including error clarity, accessibility semantics (VoiceOver, keyboard focus order, Dynamic Type, reduced motion), and cognitive load (`change` or `add`).

Unlike generic multi-pass prompts that manufacture noise on later passes, Ponder rewards **epistemic silence**: all four voices must verify the evidence and speak in `deliberation-N.json`, but a voice whose findings already hold emits `keep` deltas rather than inventing nitpicks.

## Deliberation loop and CLI commands

Use the bundled zero-dependency CLI at `scripts/ponder.mjs`:

### 1. Read-only inspection (safe first run)

Inspect the bundled synthetic fixture or an existing session directory without writing any files:

```sh
node scripts/ponder.mjs inspect --session-dir fixtures/synthetic/sample-audit --format json
node scripts/ponder.mjs coverage --session-dir fixtures/synthetic/sample-audit --format json
node scripts/ponder.mjs verify --session-dir fixtures/synthetic/sample-audit --format json
```

Each read-only command returns `readOnly: true` and `writesPerformed: false`.

### 2. Initialize a bounded session

When the user has approved an explicit `--session-dir`:

```sh
node scripts/ponder.mjs init --session-dir <SESSION_DIR> --goal "Audit declared auth module" --lens code --min-passes 2
```

### 3. Enumerate units exhaustively

Write `units.json` inside the declared session directory or pass a declared units file:

```sh
node scripts/ponder.mjs enumerate --session-dir <SESSION_DIR> --units-file <UNITS_JSON>
```

Every unit requires `id`, `path`, `kind`, and `label`.

### 4. Record each pass (`deliberation-N.json` + `deltas-N.jsonl`)

On each pass `N`:

1. Inspect every enumerated unit in the declared source scope.
2. Record all four perspective arguments (`skeptic`, `builder`, `dreamer`, `empath`) in `deliberation-N.json`.
3. Record delta operations in `deltas-N.jsonl`:
   - `add`: add a new finding (`id`, `unit`, `severity` in `must|should|could|note`, `confidence` in `0..1`, `title`, `detail`, `evidence`, `recommendation`, `perspective`, `why`)
   - `change`: update an existing finding's `severity`, `confidence`, `title`, `detail`, `evidence`, or `recommendation` with `perspective` and `why`
   - `retract`: retract an unsupported finding (`id`, `perspective`, `why`)
   - `keep`: confirm an existing finding holds after re-verification (`id`, `perspective`, `why`)
   - `unit-add`: add a newly discovered unit within the declared source scope (`unit`, `perspective`, `why`)
4. Apply the pass to the session ledger:

```sh
node scripts/ponder.mjs pass --session-dir <SESSION_DIR> --pass 1 --deliberation-file <DELIB_JSON> --deltas-file <DELTAS_JSONL>
```

### 5. Verify convergence and emit report

Check whether stopping is honest:

```sh
node scripts/ponder.mjs verify --session-dir <SESSION_DIR> --format json
```

Stopping is allowed (`canStop: true`, `verdict: "converged"`) only when:

- `passCount >= minPasses` (at least 2 passes completed);
- `coverage.ratio === 1` (every enumerated unit has at least one live finding or explicit grade);
- `deliberationGate.ok === true` (all four voices recorded non-empty arguments on every pass);
- `latestPass.mutatingDeltas === 0` (the latest pass produced zero `add`, `change`, `retract`, or `unit-add` deltas, confirming stability);
- every open `must` finding has `confidence >= 0.5`.

When `verify` passes, generate the markdown summary inside the declared `--session-dir`:

```sh
node scripts/ponder.mjs report --session-dir <SESSION_DIR> --output report.md
```

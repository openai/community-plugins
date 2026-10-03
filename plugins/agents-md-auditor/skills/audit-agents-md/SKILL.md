---
name: audit-agents-md
description: Read-only audit of AGENTS.md and AGENTS.override.md files against the repository they describe, combining deterministic checks with a short quality review.
---

# Audit AGENTS.md

Check whether a repository's agent instructions still match the repository. This is a read-only skill: it reports findings and proposes fixes, and never edits.

## Safety contract

- This skill is read-only. Do not edit, create, delete, rename, or format any file.
- Do not run the commands, scripts, or make targets that AGENTS.md documents. Check that they exist; do not execute them.
- Do not install dependencies, write caches, or contact the network.
- Audit only the declared repository root (the current workspace unless the user names another directory). Do not read `~/.codex/AGENTS.md` or other home-directory files unless the user explicitly asks.
- If a finding reports a credential-shaped value, quote only the redacted form from the tool output. Never repeat the value.

## Deterministic checks

Run the bundled checker from this plugin. It lives at `scripts/agents-md-audit.mjs`, two directories above this file:

```sh
node <plugin-root>/scripts/agents-md-audit.mjs audit --root <repository-root> --format json
```

Confirm the output reports `"readOnly": true` and `"writesPerformed": false`. If the script is missing or Node.js 22.19+ is unavailable, say so and do the quality review manually instead, stating that the deterministic checks were not run.

The checker reports, per file and line:

- **errors**: `unknown-script`, `no-package-json`, `unknown-make-target`, `no-makefile`, `broken-link`, `secret-like`;
- **warnings**: `broken-path`, `package-manager-mismatch`, `oversize-file`, `oversize-chain`, `duplicate-heading`, `empty-file`, `discovery-limit`;
- **info**: `override-shadows`, `repeated-from-parent`, `vague-instruction`, `missing-root`, `path-outside-root`.

`node <plugin-root>/scripts/agents-md-audit.mjs rules` prints the full rule list.

For each error or warning, open the cited line and the referenced file or manifest before reporting it, and confirm it is a real problem. The checker uses heuristics for inline-code paths; if a `broken-path` finding is clearly not a path (for example, a branch name), say it is a false positive rather than proposing a fix.

## Quality review

After the deterministic checks, read each effective instruction file once and assess briefly:

1. **Commands**: are install, build, test, and lint commands present and copy-paste ready?
2. **Layout**: does it say where the main code, tests, and configuration live?
3. **Non-obvious rules**: does it record gotchas, ordering constraints, or conventions an agent could not infer from the code?
4. **Scope**: does each nested file add only what differs from its parent?
5. **Signal**: is anything generic, outdated, or long enough to crowd out the useful parts?

Keep this review short and specific to the repository. Do not pad it with generic advice.

## Output

Return:

- a one-line summary with the file count and error/warning/info totals;
- findings grouped by file, each with line, rule, a plain explanation, and a concrete proposed fix;
- the quality-review notes, one line per point that needs attention;
- a proposed patch for the AGENTS files only, shown as a unified diff and clearly marked as not applied.

End by asking which findings to fix. Hand approved fixes to `$revise-agents-md`; do not apply them yourself.

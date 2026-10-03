# AGENTS.md Auditor

AGENTS.md files go stale quietly: a script gets renamed, a directory moves, a
nested file starts repeating its parent. Codex keeps following the old
instructions. This plugin checks every `AGENTS.md` and `AGENTS.override.md` in
a repository against the files that actually exist, then helps you fix what it
finds.

It has two skills:

| Skill | Writes? | Purpose |
| --- | --- | --- |
| `$audit-agents-md` | No | Run the deterministic checker, verify each finding, add a short quality review, and propose a diff. |
| `$revise-agents-md` | AGENTS files only, after approval | Apply approved fixes or learnings from the current task, then re-run the audit. |

Both skills are explicit-only (`allow_implicit_invocation: false`).

## Safe first run

Requires Codex and Node.js 22.19 or newer. Install from the marketplace:

```sh
codex plugin marketplace add openai/community-plugins \
  --ref main \
  --sparse .agents/plugins \
  --sparse plugins/agents-md-auditor
codex plugin add agents-md-auditor@community-plugins
```

Start a new Codex task in the repository you want to check:

```text
Use $audit-agents-md to audit the AGENTS.md files in this repository and explain each finding. Do not edit files yet.
```

To try it without touching your own code, point it at the bundled synthetic
fixture:

```sh
node plugins/agents-md-auditor/scripts/agents-md-audit.mjs audit \
  --root plugins/agents-md-auditor/fixtures/synthetic/stale
```

## What it checks

| Rule | Severity | What it means |
| --- | --- | --- |
| `unknown-script` | error | `npm run x`, `pnpm run x`, `yarn run x`, `bun run x`, or `npm test`/`start` names a script missing from the nearest `package.json`. |
| `no-package-json` | error | A package script is documented, but no `package.json` exists up to the root. |
| `unknown-make-target` | error | `make x` names a target missing from the nearest Makefile (a warning when the Makefile uses `include` or pattern rules). |
| `no-makefile` | error | A make target is documented, but no Makefile exists up to the root. |
| `broken-link` | error | A relative Markdown link points nowhere. |
| `secret-like` | error | A credential-shaped value appears in the file. Output shows only a redacted prefix. |
| `broken-path` | warning | An inline-code path such as `` `src/legacy/router.js` `` exists neither next to the file nor at the root. |
| `package-manager-mismatch` | warning | Commands use `yarn` while the lockfile is `pnpm-lock.yaml`, for example. |
| `oversize-file` / `oversize-chain` | warning | One file, or the root-to-directory chain Codex combines, exceeds the byte budget (default 32 KiB; change with `--max-bytes` to match your `project_doc_max_bytes`). |
| `duplicate-heading`, `empty-file` | warning | Structural problems within one file. |
| `override-shadows` | info | `AGENTS.override.md` sits next to `AGENTS.md`, so edits to `AGENTS.md` in that directory have no effect. |
| `repeated-from-parent` | info | A nested file repeats a line its parent already states. |
| `vague-instruction` | info | Lines like "follow best practices" give the agent nothing to act on. |
| `missing-root`, `path-outside-root`, `discovery-limit` | info / warning | Coverage notes. |

Commands are followed through `cd dir && ...` within the repository.
Workspace-targeted commands (`--filter`, `-w`, `--prefix`, `make -C`) are
skipped instead of guessed at. Existence checks are case-sensitive on every
operating system, so results on macOS match Linux CI.

The checker can also run on its own, for example in CI:

```sh
node scripts/agents-md-audit.mjs audit --root . --format json --fail-on warning
node scripts/agents-md-audit.mjs rules
```

Exit codes: `0` means no findings at or above `--fail-on` (default `error`),
`1` means there are some, and `2` means a usage or input error.

## Permissions and authentication

The manifest declares `Read` and `Write`.

- **Read**: the checker reads instruction files, `package.json`, Makefiles,
  lockfile names, and directory listings under the declared root.
- **Write**: only `$revise-agents-md` writes, only to `AGENTS.md` or
  `AGENTS.override.md` inside the declared root, and only hunks you approve
  after seeing the diff. The checker script itself never writes.

Authentication policy is `ON_USE`, and there is nothing to authenticate. The
plugin has no app, MCP server, account, or credential.

## Data boundaries

- No network access, telemetry, or model calls from the script.
- Documented commands are parsed and never executed.
- Symlinks are never followed during discovery, and references that resolve
  outside the root are reported (`path-outside-root`) without being inspected.
- `node_modules`, `.git`, `dist`, `build`, virtual environments, and similar
  generated directories are skipped.
- Output uses root-relative paths, so reports do not leak absolute home
  directories.
- Credential-shaped values are redacted in all output.
- Each file is read up to 1 MiB, and discovery stops at 200 instruction files
  (`--max-files`).

## Failure behavior

- Missing or unreadable root: exit `2` with a short message and no partial report.
- No instruction files: exit `0` with a note that nothing was found.
- An unparseable `package.json` is skipped rather than reported as missing scripts.
- If Node.js is unavailable, `$audit-agents-md` states that the deterministic
  checks were not run and falls back to a manual review.

## Limitations

- Path detection in inline code is heuristic. A token is checked only when it
  contains a `/` and has a `./` prefix, a known file extension, or a first
  segment that exists. Bare filenames (`index.js`), placeholders (`<name>`),
  globs, and paths under build-output directories such as `dist/` are skipped.
  Some stale references are therefore missed, and an occasional false positive
  is possible. The audit skill verifies each finding before reporting it.
- Scripts are checked against the nearest `package.json` only. Workspace
  protocols, `npx` binaries, task runners other than make, and shell aliases
  are not resolved.
- The oversize checks count only repository files. Your global
  `~/.codex/AGENTS.md` also counts toward Codex's budget but is not read.
- Override semantics follow the documented Codex behavior for
  `AGENTS.override.md`. Alternative filenames configured through
  `project_doc_fallback_filenames` are not discovered.

## Tests

From the repository root:

```sh
npm run test:agents-md-auditor
```

See [the test guide](../../tests/agents-md-auditor/README.md).

## Credits

The audit-then-revise workflow was inspired by the Apache-2.0
[`claude-md-management`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/claude-md-management)
plugin for Claude Code. This plugin is an independent implementation for Codex
and AGENTS.md; no code or text was copied from it.

## License

Apache-2.0. See [LICENSE](LICENSE).

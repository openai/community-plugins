---
name: revise-agents-md
description: Apply explicitly approved changes to AGENTS.md or AGENTS.override.md files, including audit fixes and learnings from the current task, then re-run the audit to confirm.
---

# Revise AGENTS.md

Turn approved audit findings, or lessons learned during the current task, into small edits to the repository's AGENTS files.

## Safety contract

- Write only to `AGENTS.md` or `AGENTS.override.md` files inside the declared repository root. Do not edit source, configuration, manifests, lockfiles, or any other file, even when a finding points at one.
- Do not edit `~/.codex/AGENTS.md` or any file outside the repository unless the user names that exact file and approves the change.
- Do not write anything until the user has approved the specific change. Show the proposed diff first.
- Never add credentials, tokens, private URLs, personal paths, or customer data to an instruction file.
- Do not run the documented commands to "verify" them. Existence is checked by the audit script; running them is the user's decision.

## Inputs

Use one of:

- findings approved from a `$audit-agents-md` run; or
- learnings from the current task that a future agent would need: a command that turned out to be required, a non-obvious ordering or environment constraint, or a convention the code does not make obvious.

If neither is present, stop and ask what to change.

## Method

1. Re-read the target file and the evidence for each change (the referenced `package.json`, Makefile, or directory) so the edit reflects what exists now.
2. Choose the right file. Shared project rules go in the root `AGENTS.md`. Package-specific rules go in that package's file. If an `AGENTS.override.md` shadows an `AGENTS.md` in the same directory, edit the override, or ask the user whether to merge the two.
3. Draft the smallest edit that fixes the problem:
   - replace a stale path or command with the one that exists, or delete it if nothing replaces it;
   - merge duplicate headings and delete lines already stated in a parent file;
   - replace vague rules with the concrete command or convention, or delete them;
   - keep additions to one line per fact, written as an instruction.
4. Show the full unified diff and wait for approval. Apply only the approved hunks.
5. Re-run the checker on the same root and report the before and after totals:

   ```sh
   node <plugin-root>/scripts/agents-md-audit.mjs audit --root <repository-root>
   ```

   If new errors appear, report them and propose a follow-up diff instead of editing again without approval.

## Output

List the files changed, the hunks applied, any hunks skipped and why, and the audit totals before and after.

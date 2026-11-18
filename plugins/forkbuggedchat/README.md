# Fork Chat Fixed

Fixes Fork Chat for Codex desktop users signed in with an API key but not a ChatGPT account. In that mode, the built-in Fork Chat can remain stuck at `execution-config-loading` because its Statsig sidebar gate is unavailable. This plugin uses a `UserPromptSubmit` hook to call the local Codex App Server directly, before a model turn. There are no skills or background services.

## Commands

Type exactly one of these words in the chat input and submit it:

- `forkchatfixed` forks into the current working directory.
- `forkchatinnewworktreefixed` forks into a new detached Git worktree under `~/.codex/worktrees/`. It copies tracked working-tree changes and untracked files, then records the forked chat as the worktree owner.

Spaces, backticks, a slash prefix, extra text, and the former command `forkbuggedchat` do not trigger the hook. The submitted command is blocked from the model and is omitted from the fork.

## Requirements and behavior

Codex desktop with plugin hooks enabled, local Node.js and the Codex App Server are required. The worktree command also requires Git and a repository with an existing commit. The plugin runs local Git commands, reads and copies files in the source Git working tree, starts a local App Server process, and opens the new conversation through `codex://threads/...`. It makes no network requests of its own; Codex may make its normal requests when a new chat is used. No secrets are stored by the plugin.

The hook reports a failure in the original chat when Git or the App Server fails. If a failure occurs after the Git checkout was created, the new worktree may remain under `~/.codex/worktrees/` for inspection. On non-Windows systems a successful fork might require opening the printed chat URL manually.

The automated tests exercise prompt matching, the App Server request shape, and worktree contents with a stub server. They do not exercise the desktop sidebar.

# Reviewer verification

The canonical root master is `npm run test:codex-reviewer`, also invoked by the
catalog dispatcher. Install the plugin's locked dependencies first with
`npm --prefix plugins/codex-reviewer ci --ignore-scripts`.

The master runs, in order:

1. TypeScript checks and a production build, verifying SHA-256 equality of all
   three committed distribution artifacts against the rebuilt source.
2. The existing plugin-owned unit suite (Git snapshots, worktrees, source views,
   file icons, navigation, branch sorting/search, context and manifest).
3. Root package, integration and security checks in the corresponding folders.
   These use Node built-ins and synthetic Git fixtures only, and also run
   standalone with `npm run test:codex-reviewer:package` before npm ci.
4. The plugin-owned MCP E2E suite, including a runtime copy with no source or
   dependencies, branch comparison, unborn repositories and optional host gates.

Temporary repositories contain invented text and Git identities, never user
repositories or credentials. Stdio servers are closed before fixture removal.

Optional E2E gates explicitly skip unless configured:

- `CODEX_HOST_CLI`: installed desktop CLI executable. Checks app-server tool
  discovery, thread-only entrypoint, fullscreen resource and a synthetic task's
  context. This does not click or visually qualify desktop controls.
- `CODEX_MARKETPLACE_REPOSITORY` and `CODEX_HOST_CLI`: installs the Git marketplace
  into an isolated temporary CODEX_HOME and starts its actual packaged runtime.
  Optional `CODEX_MARKETPLACE_REF` selects a contribution branch before merge.
- An installed-cache check skips when this marketplace/version is not installed.
- `CODEX_CONTEXT_THREAD_ID` is optional and intended only for a project task
  whose cwd equals the plugin folder. Do not set it for an isolated host check.

The single Reviewer workflow runs the same master sequentially on Node 22.19,
24 and 26 on Linux, with an audit of runtime dependencies. CI requires no Codex
login or real projects. Windows/macOS desktop rendering and pointer interaction
are separate manual host qualification; a CI skip is not evidence they passed.

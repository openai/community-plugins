# Perform Action Toolkit

Perform is the `toolkit` Codex plugin. Invoke `$toolkit:perform` to discover reusable actions, select an exact variant, bind required variables, and execute the displayed workflow through Codex.

Perform requires stable Codex CLI 0.137.0+ and Python 3.6+ using only the standard library. Git is an optional repository-discovery aid with a marker-walk fallback. It supports Ubuntu 18.04+ and macOS 14+; native Windows and WSL are not supported.

## Data and permissions

Perform reads bundled action JSON and applicable system, user, and repository action files. Its Python scripts list, inspect, and render actions; they do not execute the selected action. The skill displays the selected source, notes, and exact prompt before Codex carries out that prompt in the current session. An action may read or change repository and local files, call tools, or access networks according to its definition and the active Codex permissions. Repository content and action prompts may be sent to the user's configured Codex provider during execution. Review the displayed prompt and source before allowing an unfamiliar action to run.

Prompt-variable values passed with `--var` are process arguments and can appear in process inspection or audit records. Use references to protected values rather than secret contents. Perform has no hosted service, plugin-owned account, authentication flow, telemetry collector, credential store, or external data store. The separately installed `codex-perform` Python companion is optional and is not included in this plugin package.

## Selection and local files

Action sources apply from bundled to system to user to repository; later JSON filenames within one source have higher precedence. Before execution, Perform shows the latest defining JSON file and its source order. Fields can also be inherited from earlier files. A strict `ACTION[LANGUAGE]` request never substitutes another variant. A malformed or unreadable applicable action file blocks configured actions because its intended overrides are unknown. An identifiable invalid variant blocks that selector and ambiguous selection of its action name; an unrelated valid action or an explicitly selected valid sibling variant can still run. Diagnostics identify the file or variant to fix.

The `update-action-catalogue` action writes a generated Markdown catalog only when requested. Its default target is `<repository-root>/.codex/toolkit_perform_actions/action_catalogue.md`; a user-supplied output path can be elsewhere. The writer creates an absent or empty target, replaces only a file carrying its generated marker, refuses a symbolic link at the final target, and uses a same-directory temporary file for atomic replacement. It does not delete action files. The resulting catalog stays on the local filesystem until the user removes it.

- [Complete Perform guide](https://github.com/pallgeuer/la-dev-codex-plugins/blob/main/docs/codex_perform.md)
- [Installation](https://github.com/pallgeuer/la-dev-codex-plugins/blob/main/docs/installation.md)
- [Privacy](https://github.com/pallgeuer/la-dev-codex-plugins/blob/main/PRIVACY.md)
- [Terms](https://github.com/pallgeuer/la-dev-codex-plugins/blob/main/TERMS.md)
- [Security](https://github.com/pallgeuer/la-dev-codex-plugins/blob/main/SECURITY.md)
- [Support](https://github.com/pallgeuer/la-dev-codex-plugins/issues)

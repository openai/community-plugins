# Electron Agent Debug

Version 1.0.4 provides an Agent Skill and its self-contained JavaScript MCP runtime for attach-only diagnosis of a running local Electron application. Four tools return main-process Inspector and renderer CDP state, targets, logs and exceptions. The Agent supplies the application, regression steps and dynamic loopback endpoints, then decides what the evidence proves.

## Install and prerequisites

After the PR is accepted, install the complete plugin from the community marketplace. Preserve the entire skills/electron-debug directory, including runtime, references, VERSION.md and third-party licenses. Node.js 22+ is required. Node, Electron, native binaries and optional native accelerators are not shipped.

Installing the plugin does not register or launch MCP. Normal use requires no source download, dependency installation or build. Verify the package origin through an approved installation channel before running package code.

## Safe first run

Use this prompt after preparing the project:

`Use $electron-debug to verify this Electron change. Use the project's existing commands and actual local Inspector/CDP endpoints. Verify the bundled runtime, configure the host as needed, collect evidence and run the project regression. Report BLOCKED if a necessary prerequisite cannot be supplied.`

The Agent determines the installed Skill's absolute path and runs:

```text
node <absolute-skill-directory>/runtime/launch.cjs --verify
```

On success, configure the host's local stdio MCP entry to run the same launcher without --verify. Refresh the host and confirm electron_attach, electron_status, electron_targets and electron_logs are visible. Host configuration paths, application commands, ports and target selectors remain environment-specific. Full setup, migration and provenance instructions are in [the bundled setup reference](skills/electron-debug/references/mcp-setup.md).

## Permissions, execution and data

The interface declares Read, Write and Execute to disclose the workflow: read project/configuration files; update the host's MCP configuration when authorized; launch the bundled Node.js stdio process; and use the project's existing commands for preparation and regression. These labels do not grant permissions or bypass host approval. The MCP server itself does not write project files, execute page code, inspect DOM, take screenshots, set breakpoints or manage Electron's lifecycle.

No install hooks, credentials, OAuth flow or remote service are included. No project files or diagnostic logs are uploaded. The runtime connects only to Agent-provided loopback HTTP(S) discovery endpoints and loopback WebSockets, and retains bounded events in memory. Do not supply production endpoints or private logs.

The launcher checks the manifest and SHA-256 hashes before loading MCP. Missing or inconsistent files produce PACKAGE_INTEGRITY_ERROR and no server launch. Same-package hashes detect inconsistency; they do not independently establish publisher identity or code safety. Missing application, endpoint, target or regression prerequisites produce BLOCKED rather than a code-failure verdict.

## Validation

Run `npm run test:electron-agent-debug` at the marketplace root. This master command validates metadata and exercises an archived package in isolated project/global layouts, all four MCP tools with network and external module loading prohibited, and corruption/missing-file rejection.

The [source release](https://github.com/longhun12346/electron-agent-debug/releases/tag/skill-v1.0.4) supplies build-provenance verification, cross-platform CI and real Electron fixture evidence. These source checks are separate from marketplace package tests and do not prove an installed Codex session or a caller's business regression passed.

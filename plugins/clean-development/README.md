# Clean Development for Codex

Clean Development keeps supported local developer caches and build output under a user-chosen managed root. This Codex package contains an explicit-only management skill and the local CLI runtime for version 0.2.1.

## First safe prompt

Ask: `Use $clean-development to inspect my current storage status and doctor output; do not make changes.`

The skill runs only when explicitly invoked. It does not provide MCP servers, automatic hooks, prompt bootstrap text, or model/tool calls. A normal `status --json` or `doctor --json` request is read-only.

## Requirements and setup

Node.js 20.12 or later and a Codex host with local command access are required. This package is not claimed to be available from npm: version 0.2.1 was not published when this package was prepared.

Its copied `package.json` is immutable source runtime metadata used to verify durable runtime copies. It is not a standalone npm distribution in this marketplace package; use the bundled CLI path below rather than `npm install` or package scripts.

Use the bundled CLI directly from this installed package if it is not on `PATH`:

```sh
node /absolute/path/to/clean-development/bin/clean-development.js session --dry-run --json
```

Before setup, preview the exact root and integrations:

```sh
clean-development setup --dry-run --root /Volumes/DevCache/clean-development --agents codex
```

`setup` writes local Clean Development configuration, a durable copy of its runtime, and only selected native integrations. It never moves existing files. It requires filesystem write permission to the chosen managed root and Clean Development's application-data/config locations. Network access, registry authentication, and package installation are not required by the bundled runtime.

## Session consent and data boundary

Run `clean-development session --dry-run --json` before routing a repository. Choose session-only to write external managed storage without project configuration, save project settings to create the exact reviewed `.clean-development.json`, or skip. Existing project settings and explicit environment overrides are preserved.

Managed records can contain local paths, workspace identifiers, timestamps, and leases. The CLI has no telemetry or publisher-operated backend. Commands run through it retain their own network, credentials, and privacy behavior. See [PRIVACY.md](PRIVACY.md), [SUPPORT.md](SUPPORT.md), and [TERMS.md](TERMS.md).

## Cleanup

`prune` previews by default and considers only registered, direct children of a managed build root. It skips active and pinned workspaces. Use `--apply` only after reviewing the preview and authorizing deletion.

```sh
clean-development prune --older-than 60d --json
clean-development prune --older-than 60d --apply
```

This is routing, not a filesystem sandbox. A tool can still write an absolute path elsewhere, and host-specific integration acceptance is outside this package's local tests.

`clean-development uninstall` removes only its owned integrations and runtime files, subject to ownership checks. Managed caches and build data remain; removing the Codex plugin alone also leaves that data in place.

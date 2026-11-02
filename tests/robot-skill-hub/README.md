# Robot Skill Hub package and MCP contracts

Node >=22.19.0 is required. The master needs no npm dependencies:

```sh
npm run test:robot-skill-hub
```

It checks the actual distribution, preserves all four original portable files
in full (both JSON files, README and MIT license), and hashes the separate
COMMUNITY.md in full. It cold-copies the package and executes the actual
marketplace validator against that copy, and exercises the actual dispatcher
with a test-owned executor.
The executor records selection only; it does not run or qualify other plugins.
HTTP fixtures exercise the test-side protocol probe's selected-version handoff,
opt-out headers and safe failures. They are synthetic and prove no live service,
Codex installation, agent adoption or robot result. No test helper is bundled.

The package ships metadata, README and connection configuration only. Read
capability describes its three public read-only tools; client-controlled local
configuration storage is separate. The native fallback supplements the preserved
Agent Plugins root format to satisfy this repository's current validator.

An explicit later live qualification can run the same master with
RSH_PLUGIN_LIVE_MCP=1 in its process environment. It makes exactly six anonymous
MCP messages to the packaged endpoint: initialize, initialized notification,
tool listing, public joint-state search, version-pinned inspection and capabilities.
It sends DNT: 1 and X-RSH-Telemetry: off, supplies no credentials, disables
redirects, bounds each request, and makes no retry. Exact message transcripts
appear in the live test diagnostic. It does not fetch or execute robot source.
That opt-in is unset in ordinary marketplace and plugin CI. A skip is not a pass
for the live gate.

Run the repository-required validate:marketplace, test:marketplace and validate
commands separately after reviewing their broader execution paths. They include
other plugins, locked dependencies, local builds and platform requirements; this
dependency-free package does not make those broader suites dependency-free.

Actual cold Codex client recognition, configuration loading and MCP tool use
remain unverified. No model task, provider credential, keychain, global plugin
configuration, paid entitlement or dummy setup skill is part of these tests.
The review checklist's setup-skill step needs maintainer applicability review for
this intentionally MCP-only package. Community acceptance and official directory
listing require separate decisions; no CLA signature or maintainer approval is
claimed here.

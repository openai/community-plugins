# Robot Skill Hub: read-only MCP plugin

Maintained by [Superfast Robotics](https://github.com/Superfast-Robotics).
This Agent Plugins 1.0.0 package connects a compatible client to the public
Robot Skill Hub Streamable HTTP endpoint. No Hub account, token or API key is
required for these public reads.

## Tools

| Tool | Purpose |
| --- | --- |
| `find_robot_skills` | Find pinned robot code using task text and explicit robot, runtime or environment filters. |
| `inspect_robot_skill` | Read one selected source or recipe, license, requirements and exact evidence. Preserve the search result's `record_version`. |
| `get_hub_capabilities` | Read current discovery capabilities and the separate CLI onboarding contract. |

Start with a focused search, then inspect the selected record before integrating
its code. Match the source revision, recipe version, controller and environment
to the intended project. Source references with `hub_prepare: false` provide
source discovery rather than a runnable Hub adapter.

Positive milestones and task outcomes are separate. For example,
`import_verified` can coexist with
`evidence.hub_verification.last_task_attempt.status: failed`. Read the observed
outcome, its exact pins and immutable evidence; do not infer successful robot
operation from a search match, an import check or an inspection's `ready` status.
One recorded failure does not establish a cause or failure in other environments.

## Connection and execution boundary

Load this directory through a client that supports Agent Plugins 1.0.0. Its
`mcp.json` declares one `streamable-http` connection:
`https://api.superfastrobotics.com/mcp`. The client sends the requested MCP search
and inspection inputs to that public service. Do not include private project
data or credentials in public queries.

For operator checks or an optional measurement opt-out, configure the client's
HTTP headers as `DNT: 1` and `X-RSH-Telemetry: off`. The default configuration
supplies no identity, authentication or opt-out headers.

The package contains metadata and this connection configuration. It ships no
scripts, local server, hooks, command, credentials, CLI bundle or catalog snapshot.
It does not install dependencies, fetch source files, run a simulator, control a
robot or submit contributions. Tool responses are data, not permission to execute
or publish work.

Source acquisition and execution preparation use the separate
[Robot Skill Hub Python CLI](https://github.com/Superfast-Robotics/robot-skill-hub/tree/main/skills/find-robot-skills).
Preparation, dependency installation and robot execution are separate operations
requiring the caller's authorization and a matching documented environment.
Contribution features also use that separate CLI and their own explicit sharing
scope and credentials; this MCP plugin provides no write tool.

## Compatibility and license

The manifest and MCP configuration use the official Agent Plugins 1.0.0 schemas.
GitHub Copilot installation and tool use for this package have not been verified
locally. Existing Hub API/MCP checks do not establish client integration, agent
adoption, task completion or physical hardware compatibility. Plugin version
`0.1.0` identifies this connection package separately from the hosted service.

Package license: [MIT](LICENSE). Upstream robot code discovered through the Hub
retains its own per-record license and verification boundaries.

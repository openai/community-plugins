
## First safe prompt

Find public robot code for joint state broadcasting in ROS 2 controllers.
Inspect the selected result using its returned record_version. Report the
original source revision, license, compatibility and task evidence. Do not
download or execute source, install dependencies, submit findings or send private
project files.

The input above is a public task description. For another task, supply only
operator-authorized public task and environment labels. Do not attach source
files, transcripts, credentials or private workspace contents to public queries.

## Community marketplace package

This Community copy retains the portable Agent Plugins 1.0.0 root plugin.json
and mcp.json. Its additional .codex-plugin/plugin.json supplies the metadata and
MCP configuration path required by this repository's validator. This addition
has not been tested in a Codex client; schema/configuration validation and live
MCP service checks are separate evidence.

Prerequisites are a compatible Codex client and HTTPS access to
https://api.superfastrobotics.com/mcp. The package requires no Hub account,
submission token, provider API key, local Python installation, robot, GPU or
paid Hub plan. Client access and entitlement remain subject to that client's
own requirements; this proposal has not established a free client entitlement.

The marketplace's ON_USE authentication policy is a catalog convention. These
three Hub tools perform anonymous public reads and must not request a Hub token.
The package declares no local command, hook, script, environment variable or
credential header. A compatible client reads package metadata and sends the
explicit MCP inputs to the public endpoint. Its own plugin/configuration storage
and protocol behavior remain client-controlled.

## Safe failures

If the endpoint is unreachable, its protocol is unsupported or the service
reports a rate or quota limit, report the actual error and stop that operation.
Do not invent a result, switch endpoints, request a submission credential or
install a local server. If no record matches, broaden only the explicitly
authorized public query or report no match. Unknown compatibility and
hub_prepare: false are useful source-discovery results, not execution approval.

No Codex installed-client result, maintainer approval, Community listing or
official Plugins Directory listing is claimed by this proposal.

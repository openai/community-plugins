# Datacircle

Find the best GTM APIs. Datacircle benchmarks all the GTM APIs and puts the results on its website, [datacircle.dev](https://datacircle.dev).

The plugin adds Datacircle's MCP server and a skill.

## First safe prompt

"Use Datacircle to list the GTM APIs it benchmarks."

## Prerequisites

None. The server is open to anyone, with no key.

## Permissions and authentication

The MCP server is `https://api.datacircle.dev/mcp`. It has two read-only tools, `list_apis` and `search_plans`. No sign-in. The skill downloads one public file, `https://datacircle.dev/datacircle.json`.

## Data boundaries

The plugin sends only the API name or search you ask for. It reads no local file and writes nothing.

## Failure behavior

If the server or the file does not answer, the tools return an error and the plugin changes nothing. Try again later or open https://datacircle.dev.

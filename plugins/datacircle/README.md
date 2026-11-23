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

The plugin sends only the API name or search you ask for. The skill writes one file, `datacircle.json`, in the folder it runs in, and reads no other local file.

## Failure behavior

If the server does not answer, the tools return an error. If the file's download fails, the skill keeps your last `datacircle.json` as it was. Try again later or open https://datacircle.dev.

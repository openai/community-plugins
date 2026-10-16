import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const readJson = async (path) => JSON.parse(await readFile(new URL(path, import.meta.url), "utf8"));

test("portable plugin manifest identifies the Code Review plugin", async () => {
  const manifest = await readJson("../plugin.json");

  assert.equal(manifest.name, "codex-reviewer");
  assert.equal(manifest.license, "MIT");
  assert.equal(manifest.extensions["com.openai"].interface.displayName, "Reviewer");
  assert.match(manifest.version, /^\d+\.\d+\.\d+$/);
  assert.ok(manifest.description);
});

test("stdio server command points to the built local MCP server", async () => {
  const config = await readJson("../mcp.json");
  const [server] = Object.values(config.mcpServers);

  assert.equal(server.type, "stdio");
  assert.equal(server.command, "node");
  assert.ok(server.args.some((arg) => arg.includes("${PLUGIN_ROOT}") && arg.endsWith("dist/server/index.js")));
});

test("server exposes only a thread review entrypoint", async () => {
  const source = await readFile(new URL("../src/server/index.ts", import.meta.url), "utf8");

  assert.ok(!source.includes('type: "global"'));
  assert.ok(source.includes('type: "thread"'));
  assert.ok(source.includes("server.registerResource"));
  assert.ok(source.includes("server.registerTool"));
});

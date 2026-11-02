import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../../../plugins/codex-reviewer/", import.meta.url);
const json = async (file) => JSON.parse(await readFile(new URL(file, root), "utf8"));

test("Reviewer has matching portable and Codex manifests with MIT licensing", async () => {
  const portable = await json("plugin.json");
  const codex = await json(".codex-plugin/plugin.json");
  const pkg = await json("package.json");
  for (const key of ["name", "version", "license", "repository", "author"]) {
    assert.deepEqual(codex[key], portable[key], key);
  }
  assert.equal(portable.name, "codex-reviewer");
  assert.equal(portable.license, "MIT");
  assert.equal(pkg.license, "MIT");
  assert.equal(pkg.version, portable.version);
  assert.deepEqual(codex.interface, portable.extensions["com.openai"].interface);
  assert.deepEqual(codex.interface.capabilities, ["Read"]);
  assert.equal(codex.mcpServers, "./mcp.json");
  assert.match(await readFile(new URL("LICENSE", root), "utf8"), /MIT License/);
  const notices = await readFile(new URL("dist/THIRD_PARTY_NOTICES.txt", root), "utf8");
  for (const name of ["monaco-editor", "react", "@modelcontextprotocol/server", "@openai/mcp-extensions", "VS Code Seti"]) {
    assert.ok(notices.includes(name), name);
  }
});

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const plugin = path.join(root, "plugins", "datacircle");
const read = (file) => fs.readFileSync(path.join(plugin, file), "utf8");

test("manifest names the plugin and its parts", () => {
  const manifest = JSON.parse(read(".codex-plugin/plugin.json"));
  assert.equal(manifest.name, "datacircle");
  assert.equal(manifest.homepage, "https://datacircle.dev");
  assert.equal(manifest.skills, "./skills/");
  assert.equal(manifest.mcpServers, "./.mcp.json");
  for (const file of [manifest.interface.logo, manifest.interface.composerIcon]) {
    assert.ok(fs.existsSync(path.join(plugin, file)), file);
  }
});

test("the MCP server is Datacircle's open HTTPS endpoint, with no credential", () => {
  const config = JSON.parse(read(".mcp.json"));
  const server = config.mcpServers.datacircle;
  assert.equal(server.url, "https://api.datacircle.dev/mcp");
  assert.deepEqual(Object.keys(server), ["url"]);
});

test("the skill has its front matter and README states the boundaries", () => {
  assert.match(read("skills/datacircle/SKILL.md"), /^---\nname: datacircle\ndescription: .+\n---/);
  for (const heading of ["First safe prompt", "Prerequisites", "Permissions and authentication", "Data boundaries", "Failure behavior"]) {
    assert.ok(read("README.md").includes(`## ${heading}`), heading);
  }
});

test("no secret or personal path is committed", () => {
  const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(path.join(dir, e.name)) : [path.join(dir, e.name)]);
  for (const file of walk(plugin).filter((f) => !f.endsWith(".png"))) {
    const text = fs.readFileSync(file, "utf8");
    assert.doesNotMatch(text, /\/home\/|\/Users\/|sk-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY/, file);
  }
});

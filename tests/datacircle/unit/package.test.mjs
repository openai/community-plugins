import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
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

test("a download cut halfway keeps the last datacircle.json", async () => {
  const skill = read("skills/datacircle/SKILL.md");
  const block = skill.split("## 1. Download it")[1].match(/```bash\n([\s\S]*?)```/)[1];
  assert.ok(block.includes("https://datacircle.dev/datacircle.json"));
  const server = http.createServer((req, res) => {
    res.writeHead(200, { "content-type": "application/json", "content-length": "1000" });
    res.write('[{"provider":');
    setTimeout(() => res.destroy(), 50);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}/datacircle.json`;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "datacircle-"));
  fs.writeFileSync(path.join(dir, "datacircle.json"), "[]");
  const status = await new Promise((resolve) =>
    spawn("bash", ["-c", block.replace("https://datacircle.dev/datacircle.json", url)], { cwd: dir, stdio: "ignore" }).on("close", resolve));
  server.close();
  assert.notEqual(status, 0);
  assert.equal(fs.readFileSync(path.join(dir, "datacircle.json"), "utf8"), "[]");
  assert.deepEqual(fs.readdirSync(dir), ["datacircle.json"]);
  fs.rmSync(dir, { recursive: true });
});

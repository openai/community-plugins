import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const pluginDir = path.join(repoRoot, "plugins", "la-review");

function walk(root) {
  const entries = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const file = path.join(root, entry.name);
    entries.push(file);
    if (entry.isDirectory()) entries.push(...walk(file));
  }
  return entries;
}

test("distribution has only regular files and no generated or secret-bearing artifacts", () => {
  for (const file of walk(pluginDir)) {
    const relative = path.relative(pluginDir, file);
    const stats = fs.lstatSync(file);
    assert.equal(stats.isSymbolicLink(), false, "symlink rejected: " + relative);
    assert.doesNotMatch(
      relative,
      /(^|\/)(node_modules|\.cache|__pycache__|\.pytest_cache|\.DS_Store|dist|build)(\/|$)/,
    );
    assert.doesNotMatch(relative, /(^|\/)(\.env(?:\..*)?|.*\.(?:pem|key|p12))$/i);
    if (!stats.isDirectory()) assert.ok(stats.isFile(), "regular file required: " + relative);
  }
});

test("package declares local skill behavior without MCP, apps, or hosted authentication", () => {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(pluginDir, ".codex-plugin", "plugin.json"), "utf8"),
  );
  const readme = fs.readFileSync(path.join(pluginDir, "README.md"), "utf8");

  assert.equal(manifest.mcpServers, undefined);
  assert.equal(manifest.apps, undefined);
  assert.match(readme, /Invoke `\$la-review:loupe`/);
  assert.match(readme, /no hosted service, authentication flow, telemetry collector, or external data store/i);
  assert.match(readme, /user's local tools, provider accounts, Codex session, and granted permissions/i);
});

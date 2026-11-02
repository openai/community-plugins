import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const validatorSource = fileURLToPath(new URL("../../scripts/validate-marketplace.mjs", import.meta.url));

function fixture(t) {
  const root = mkdtempSync(path.join(tmpdir(), "marketplace-validator-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const script = path.join(root, "scripts/validate-marketplace.mjs");
  const catalog = path.join(root, ".agents/plugins/marketplace.json");
  const plugin = path.join(root, "plugins/example-plugin");
  const manifest = path.join(plugin, ".codex-plugin/plugin.json");
  for (const file of [script, catalog, manifest]) mkdirSync(path.dirname(file), { recursive: true });
  copyFileSync(validatorSource, script);
  writeFileSync(catalog, JSON.stringify({
    name: "community-plugins",
    interface: { displayName: "Community Plugins" },
    plugins: [{
      name: "example-plugin",
      category: "Developer Tools",
      source: { source: "local", path: "./plugins/example-plugin" },
      policy: { installation: "AVAILABLE", authentication: "ON_USE" },
    }],
  }));
  writeFileSync(manifest, JSON.stringify({
    name: "example-plugin",
    version: "1.0.0",
    description: "A local validation fixture.",
    author: { name: "Test Author" },
    license: "Apache-2.0",
    interface: {
      displayName: "Example Plugin",
      shortDescription: "A validation fixture.",
      category: "Developer Tools",
      capabilities: ["Read"],
      defaultPrompt: ["Inspect the fixture."],
    },
  }));
  writeFileSync(path.join(plugin, "README.md"), "# Example Plugin\n");
  return { root, script, catalog, manifest };
}

function run(repo) {
  const result = spawnSync(process.execPath, [repo.script], {
    cwd: repo.root,
    encoding: "utf8",
    timeout: 5000,
  });
  assert.equal(result.error, undefined);
  assert.equal(result.signal, null);
  return result;
}

test("accepts a complete marketplace and plugin manifest", t => {
  const result = run(fixture(t));
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Marketplace validation passed/);
});

for (const target of ["catalog", "manifest"]) {
  for (const value of [null, false, true, 0, "", "example", []]) {
    test(`rejects ${target} with non-object JSON root ${JSON.stringify(value)}`, t => {
      const repo = fixture(t);
      writeFileSync(repo[target], JSON.stringify(value));
      const result = run(repo);
      assert.equal(result.status, 1, result.stdout + result.stderr);
      const label = target === "catalog" ? "marketplace" : "plugins[0] manifest";
      assert.ok(result.stderr.includes(`ERROR ${label} must be a JSON object`), result.stderr);
      assert.doesNotMatch(result.stdout, /Marketplace validation passed/);
    });
  }

  test(`continues rejecting malformed ${target} JSON`, t => {
    const repo = fixture(t);
    writeFileSync(repo[target], "{");
    const result = run(repo);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /is not valid JSON/);
    assert.doesNotMatch(result.stdout, /Marketplace validation passed/);
  });
}

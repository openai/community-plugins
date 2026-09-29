import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const pluginName = "toolkit";
const pluginDir = path.join(repoRoot, "plugins", pluginName);
const manifest = JSON.parse(
  fs.readFileSync(path.join(pluginDir, ".codex-plugin", "plugin.json"), "utf8"),
);
const marketplace = JSON.parse(
  fs.readFileSync(path.join(repoRoot, ".agents", "plugins", "marketplace.json"), "utf8"),
);

test("manifest and marketplace agree on the public package", () => {
  const entry = marketplace.plugins.find((plugin) => plugin.name === pluginName);
  assert.ok(entry, "catalog entry is required");
  assert.equal(entry.source.source, "local");
  assert.equal(entry.source.path, "./plugins/toolkit");
  assert.equal(entry.policy.installation, "AVAILABLE");
  assert.equal(entry.policy.authentication, "ON_INSTALL");
  assert.equal(entry.category, "Developer Tools");

  assert.equal(manifest.name, pluginName);
  assert.equal(manifest.version, "0.4.4");
  assert.equal(manifest.skills, "./skills/");
  assert.equal(manifest.interface.category, "Developer Tools");
  assert.equal(manifest.mcpServers, undefined);
  assert.equal(manifest.apps, undefined);
  assert.equal(manifest.interface.logo, "./assets/logo.svg");
  assert.equal(manifest.interface.logoDark, "./assets/logo-dark.svg");
  assert.equal(manifest.interface.composerIcon, "./assets/composer-icon.svg");
  for (const asset of ["logo.svg", "logo-dark.svg", "composer-icon.svg"]) {
    assert.ok(fs.statSync(path.join(pluginDir, "assets", asset)).isFile());
  }
});

test("Perform has explicit invocation metadata and fail-closed selection rules", () => {
  const skill = fs.readFileSync(
    path.join(pluginDir, "skills", "perform", "SKILL.md"),
    "utf8",
  );
  const metadata = fs.readFileSync(
    path.join(pluginDir, "skills", "perform", "agents", "openai.yaml"),
    "utf8",
  );

  assert.match(skill, /^---\nname: perform\n/m);
  assert.match(skill, /^description: .+$/m);
  assert.match(skill, /Select only that canonical selector; never fall back/);
  assert.match(skill, /Stop and ask for any missing value instead of inventing it/);
  assert.match(metadata, /^interface:$/m);
  assert.match(metadata, /^\s+default_prompt: "\$toolkit:perform"$/m);
  assert.match(metadata, /^policy:$/m);
  assert.match(metadata, /^\s+allow_implicit_invocation: false$/m);
});

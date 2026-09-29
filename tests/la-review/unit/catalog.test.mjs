import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const pluginName = "la-review";
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
  assert.equal(entry.source.path, "./plugins/la-review");
  assert.equal(entry.policy.installation, "AVAILABLE");
  assert.equal(entry.policy.authentication, "ON_INSTALL");
  assert.equal(entry.category, "Developer Tools");

  assert.equal(manifest.name, pluginName);
  assert.equal(manifest.version, "0.2.4");
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

test("Loupe has explicit invocation metadata and a bounded review contract", () => {
  const skill = fs.readFileSync(
    path.join(pluginDir, "skills", "loupe", "SKILL.md"),
    "utf8",
  );
  const metadata = fs.readFileSync(
    path.join(pluginDir, "skills", "loupe", "agents", "openai.yaml"),
    "utf8",
  );

  assert.match(skill, /^---\nname: loupe\n/m);
  assert.match(skill, /^description: .+$/m);
  assert.match(skill, /Do not modify repository files/);
  assert.match(skill, /A timeout or failure of one reviewer must not block/);
  assert.match(metadata, /^interface:$/m);
  assert.match(metadata, /^\s+default_prompt: "\$la-review:loupe"$/m);
  assert.match(metadata, /^policy:$/m);
  assert.match(metadata, /^\s+allow_implicit_invocation: false$/m);
});

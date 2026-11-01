import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const pluginName = "ponder";
const pluginDir = path.join(repoRoot, "plugins", pluginName);
const manifest = JSON.parse(
  fs.readFileSync(path.join(pluginDir, ".codex-plugin", "plugin.json"), "utf8"),
);
const marketplace = JSON.parse(
  fs.readFileSync(path.join(repoRoot, ".agents", "plugins", "marketplace.json"), "utf8"),
);

test("manifest and marketplace agree on the public Ponder package", () => {
  const entry = marketplace.plugins.find((plugin) => plugin.name === pluginName);
  assert.ok(entry, "catalog entry is required");
  assert.equal(entry.source.source, "local");
  assert.equal(entry.source.path, "./plugins/ponder");
  assert.equal(entry.policy.installation, "AVAILABLE");
  assert.equal(entry.policy.authentication, "ON_USE");
  assert.equal(entry.category, "Developer Tools");

  assert.equal(manifest.name, pluginName);
  assert.equal(manifest.version, "0.1.0");
  assert.equal(manifest.skills, "./skills/");
  assert.equal(manifest.interface.category, "Developer Tools");
  assert.deepEqual(manifest.interface.capabilities, ["Read", "Write"]);
  assert.equal(manifest.mcpServers, undefined);
  assert.equal(manifest.apps, undefined);
  assert.equal(manifest.interface.logo, "./assets/icon.svg");
  assert.equal(manifest.interface.composerIcon, "./assets/icon.svg");
  assert.ok(fs.statSync(path.join(pluginDir, "assets", "icon.svg")).isFile());
  assert.ok(Array.isArray(manifest.interface.defaultPrompt));
  assert.match(manifest.interface.defaultPrompt[0], /^\s*Use \$ponder\b/);
  assert.match(manifest.interface.defaultPrompt[0], /Do not edit files yet\./);
});

test("ponder skill has explicit-only metadata and safety contract", () => {
  const skillDir = path.join(pluginDir, "skills", "ponder");
  const skillMarkdown = fs.readFileSync(path.join(skillDir, "SKILL.md"), "utf8");
  const metadata = fs.readFileSync(path.join(skillDir, "agents", "openai.yaml"), "utf8");

  assert.match(skillMarkdown, /^---\nname: ponder\n/m);
  assert.match(skillMarkdown, /^description: .+$/m);
  assert.match(skillMarkdown, /^## Safety contract$/m);
  assert.match(metadata, /^interface:$/m);
  assert.match(metadata, /^\s+display_name: ".+"$/m);
  assert.match(metadata, /^\s+short_description: ".+"$/m);
  assert.match(metadata, /^\s+default_prompt: ".*\$ponder.*"$/m);
  assert.match(metadata, /^policy:$/m);
  assert.match(metadata, /^\s+allow_implicit_invocation: false$/m);
});

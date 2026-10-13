import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { pluginDir, repoRoot } from "../support/helpers.mjs";

const manifest = JSON.parse(fs.readFileSync(path.join(pluginDir, ".codex-plugin", "plugin.json"), "utf8"));
const marketplace = JSON.parse(fs.readFileSync(path.join(repoRoot, ".agents", "plugins", "marketplace.json"), "utf8"));
const packageJson = JSON.parse(fs.readFileSync(path.join(repoRoot, "package.json"), "utf8"));

test("manifest and marketplace agree on the public package", () => {
  const entry = marketplace.plugins.find((plugin) => plugin.name === "agents-md-auditor");
  assert.ok(entry, "catalog entry is required");
  assert.deepEqual(entry.source, { source: "local", path: "./plugins/agents-md-auditor" });
  assert.deepEqual(entry.policy, { installation: "AVAILABLE", authentication: "ON_USE" });
  assert.equal(entry.category, "Developer Tools");

  assert.equal(manifest.name, "agents-md-auditor");
  assert.equal(manifest.version, "0.1.0");
  assert.equal(manifest.license, "Apache-2.0");
  assert.equal(manifest.skills, "./skills/");
  assert.equal(manifest.mcpServers, undefined);
  assert.equal(manifest.apps, undefined);
  assert.deepEqual(manifest.interface.capabilities, ["Read", "Write"]);
  assert.equal(manifest.interface.category, entry.category);
  assert.match(manifest.interface.defaultPrompt[0], /^Use \$audit-agents-md\b.*Do not edit files yet\.$/);
});

test("every skill has frontmatter, a safety contract, and explicit-only metadata", () => {
  for (const skill of ["audit-agents-md", "revise-agents-md"]) {
    const dir = path.join(pluginDir, "skills", skill);
    const markdown = fs.readFileSync(path.join(dir, "SKILL.md"), "utf8");
    const metadata = fs.readFileSync(path.join(dir, "agents", "openai.yaml"), "utf8");
    assert.match(markdown, new RegExp("^---\\nname: " + skill + "\\n", "m"));
    assert.match(markdown, /^description: .+$/m);
    assert.match(markdown, /^## Safety contract$/m);
    assert.match(metadata, /^interface:$/m);
    assert.match(metadata, /^\s+default_prompt: ".*\$[a-z0-9-]+.*"$/m);
    assert.match(metadata, /^\s+allow_implicit_invocation: false$/m);
  }
});

test("root package exposes the master suite and validator", () => {
  assert.match(packageJson.scripts["test:agents-md-auditor"], /validate:agents-md-auditor/);
  assert.match(packageJson.scripts["test:agents-md-auditor"], /tests\/agents-md-auditor\/unit/);
  assert.match(packageJson.scripts["test:agents-md-auditor"], /tests\/agents-md-auditor\/integration/);
  assert.match(packageJson.scripts["test:agents-md-auditor"], /tests\/agents-md-auditor\/security/);
  assert.match(packageJson.scripts.validate, /validate:agents-md-auditor/);
});

test("README documents every rule the checker can emit", async () => {
  const { RULES } = await import("../../../plugins/agents-md-auditor/scripts/agents-md-audit.mjs");
  const readme = fs.readFileSync(path.join(pluginDir, "README.md"), "utf8");
  const skill = fs.readFileSync(path.join(pluginDir, "skills", "audit-agents-md", "SKILL.md"), "utf8");
  for (const id of Object.keys(RULES)) {
    assert.ok(readme.includes("`" + id + "`"), "README is missing rule " + id);
    assert.ok(skill.includes("`" + id + "`"), "audit skill is missing rule " + id);
  }
});

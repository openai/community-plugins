import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const repoRoot = fileURLToPath(new URL("../..", import.meta.url));
const pluginRoot = path.join(repoRoot, "plugins", "local-review-workbench");

function walk(root) {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(root, entry.name);
    assert.equal(fs.lstatSync(file).isSymbolicLink(), false, `Unexpected symlink: ${file}`);
    return entry.isDirectory() ? walk(file) : [file];
  });
}

function resolveResource(root, from, target) {
  assert.equal(path.isAbsolute(target), false, `Absolute package reference: ${target}`);
  const resolved = path.resolve(from, target);
  const relative = path.relative(root, resolved);
  assert.ok(
    relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative),
    `Reference escapes package: ${target}`,
  );
  assert.ok(fs.existsSync(resolved), `Missing package reference: ${target}`);
  return resolved;
}

function inspectResources(root) {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, ".codex-plugin/plugin.json"), "utf8"));
  const skillsRoot = resolveResource(root, root, manifest.skills);
  assert.ok(fs.statSync(skillsRoot).isDirectory());
  for (const key of ["logo", "composerIcon"]) {
    if (manifest.interface[key]) {
      assert.ok(fs.statSync(resolveResource(root, root, manifest.interface[key])).isFile());
    }
  }

  const files = walk(root);
  const skillFiles = files.filter((file) => path.basename(file) === "SKILL.md");
  assert.ok(skillFiles.length > 0, "At least one skill must be installed");
  const skillNames = new Set();
  for (const file of skillFiles) {
    const source = fs.readFileSync(file, "utf8");
    const frontmatter = source.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/)?.[1];
    assert.ok(frontmatter, `Missing skill frontmatter: ${file}`);
    const name = frontmatter.match(/^name:\s*["']?([a-z0-9-]+)["']?\s*$/m)?.[1];
    assert.ok(name, `Missing valid skill name: ${file}`);
    assert.match(frontmatter, /^description:\s*\S/m, `Missing skill description: ${file}`);
    assert.equal(skillNames.has(name), false, `Duplicate skill name: ${name}`);
    skillNames.add(name);
  }
  for (const prompt of manifest.interface.defaultPrompt) {
    for (const [, name] of prompt.matchAll(/\$([a-z0-9-]+)/g)) {
      assert.ok(skillNames.has(name), `Default prompt invokes an unbundled skill: ${name}`);
    }
  }

  // Inline Markdown links are the package's documented local-resource convention.
  for (const file of files.filter((file) => file.endsWith(".md"))) {
    const source = fs.readFileSync(file, "utf8");
    for (const [, raw] of source.matchAll(/\]\(([^\s)]+)(?:\s+"[^"]*")?\)/g)) {
      const target = raw.replace(/^<|>$/g, "");
      if (target.startsWith("#") || /^https:\/\//.test(target)) continue;
      assert.doesNotMatch(target, /^[a-z][a-z\d+.-]*:/i, `Unsupported resource URI in ${file}`);
      resolveResource(root, path.dirname(file), decodeURIComponent(target.split(/[?#]/)[0]));
    }
  }
}

test("manifest and catalog preserve an opt-in skills-only package", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(pluginRoot, ".codex-plugin/plugin.json"), "utf8"));
  const catalog = JSON.parse(fs.readFileSync(path.join(repoRoot, ".agents/plugins/marketplace.json"), "utf8"));
  const entry = catalog.plugins.find(({ name }) => name === "local-review-workbench");
  assert.ok(entry, "Plugin must be discoverable in the marketplace");
  assert.equal(manifest.name, entry.name);
  assert.deepEqual(manifest.interface.capabilities, ["Read"]);
  assert.deepEqual(entry.source, { source: "local", path: "./plugins/local-review-workbench" });
  assert.deepEqual(entry.policy, { installation: "AVAILABLE", authentication: "ON_USE" });
  for (const field of ["mcpServers", "apps", "hooks", "commands"]) {
    assert.equal(Object.hasOwn(manifest, field), false, `Unexpected executable integration: ${field}`);
  }
  assert.equal(manifest.license, "Apache-2.0");
  assert.equal(fs.readFileSync(path.join(pluginRoot, "LICENSE"), "utf8"), fs.readFileSync(path.join(repoRoot, "LICENSE"), "utf8"));
});

test("a sparse copied package resolves its skills, prompts, and local references", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "local-review-workbench-"));
  try {
    walk(pluginRoot); // Reject symlinks before copying.
    const isolatedRoot = path.join(tempRoot, "plugin");
    fs.cpSync(pluginRoot, isolatedRoot, { recursive: true });
    inspectResources(isolatedRoot);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("package contains no runtime integration or obvious private-content leaks", () => {
  const forbidden = [
    /\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}/,
    /\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})/,
    /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
    /https?:\/\/[^\s/]*(?:openai\.(?:enterprise\.)?slack\.com|openai-corpws\.slack\.com|apps\.openai\.org)\b/,
    /https?:\/\/github\.com\/openai\/(?:openai|codex-internal)(?:[\s/#?]|$)/,
    /(?:\/Users\/|\/home\/(?:dev-user|agent)\/)/,
  ];
  for (const file of walk(pluginRoot)) {
    const relative = path.relative(pluginRoot, file).split(path.sep).join("/");
    assert.doesNotMatch(relative, /(?:^|\/)(?:node_modules|scripts|hooks)(?:\/|$)/);
    assert.doesNotMatch(relative, /(?:^|\/)(?:package(?:-lock)?\.json|\.mcp\.json|\.env(?:\..*)?)$/);
    if (/\.(?:mjs|js|cjs|py|sh|wasm|node)$/.test(relative)) {
      assert.ok(relative.startsWith("fixtures/"), `Executable source outside synthetic fixtures: ${relative}`);
    }
    const source = fs.readFileSync(file, "utf8");
    for (const pattern of forbidden) {
      assert.doesNotMatch(source, pattern, `Potential private content in ${relative}`);
    }
  }
});

test("synthetic compatibility fixture reproduces the documented caller failure", async () => {
  const fixtureRoot = path.join(pluginRoot, "fixtures", "compatibility");
  const before = await import(pathToFileURL(path.join(fixtureRoot, "before", "api.mjs")));
  const after = await import(pathToFileURL(path.join(fixtureRoot, "after", "api.mjs")));
  const { widgetNames } = await import(pathToFileURL(path.join(fixtureRoot, "consumer.mjs")));
  assert.deepEqual(widgetNames(before.listWidgets()), ["Alpha", "Beta"]);
  assert.throws(() => widgetNames(after.listWidgets()), TypeError);
  const limited = after.listWidgets({ limit: 1 });
  assert.deepEqual(widgetNames({ items: limited.widgets }), ["Alpha"]);
  for (const limit of [0, 101, 1.5, "1", Infinity]) {
    assert.throws(() => after.listWidgets({ limit }), RangeError);
  }
});

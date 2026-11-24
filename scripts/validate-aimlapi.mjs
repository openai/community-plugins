#!/usr/bin/env node

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const pluginRoot = fileURLToPath(new URL("../plugins/aimlapi/", import.meta.url));

export function validatePackage(root) {
  const receipt = JSON.parse(fs.readFileSync(path.join(root, "UPSTREAM.json"), "utf8"));
  assert.equal(receipt.repository, "https://github.com/aimlapi/skills");
  assert.match(receipt.commit, /^[a-f0-9]{40}$/u);
  const expected = new Set([
    ...Object.keys(receipt.files), "UPSTREAM.json", "README.md", "THIRD_PARTY_NOTICES.txt",
  ]);
  const found = new Set();
  function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const filename = path.join(directory, entry.name);
      assert.ok(!entry.isSymbolicLink(), `symlink in package: ${filename}`);
      if (entry.isDirectory()) walk(filename);
      else {
        assert.ok(entry.isFile(), `non-regular package member: ${filename}`);
        found.add(path.relative(root, filename).split(path.sep).join("/"));
      }
    }
  }
  walk(root);
  assert.deepEqual([...found].sort(), [...expected].sort(), "unexpected or missing package files");
  for (const [relative, digest] of Object.entries(receipt.files)) {
    assert.ok(!relative.startsWith("/") && !relative.split("/").includes(".."), "invalid receipt path");
    const actual = createHash("sha256").update(fs.readFileSync(path.join(root, relative))).digest("hex");
    assert.equal(actual, digest, `upstream content changed: ${relative}`);
  }

  const manifest = JSON.parse(fs.readFileSync(path.join(root, ".codex-plugin/plugin.json"), "utf8"));
  assert.equal(manifest.name, "aimlapi");
  assert.equal(manifest.version, receipt.version);
  assert.equal(manifest.license, "Apache-2.0");
  assert.equal(manifest.repository, receipt.repository);
  assert.equal(manifest.skills, "./skills/");
  assert.deepEqual(manifest.interface.capabilities, ["Read", "Write"]);
  for (const field of ["mcpServers", "apps", "hooks"]) {
    assert.equal(manifest[field], undefined, `unexpected ${field} declaration`);
  }
  for (const field of ["logo", "composerIcon"]) {
    const relative = manifest.interface[field];
    assert.ok(relative.startsWith("./") && found.has(relative.slice(2)), `missing ${field}`);
  }

  const skills = fs.readdirSync(path.join(root, "skills"));
  assert.equal(skills.length, 13, "snapshot must include all 13 upstream skills");
  for (const skill of skills) {
    const text = fs.readFileSync(path.join(root, "skills", skill, "SKILL.md"), "utf8");
    const block = /^---\r?\n([\s\S]*?)\r?\n---\r?\n/u.exec(text)?.[1];
    assert.ok(block, `${skill}: missing frontmatter`);
    assert.equal(/^name: (.+)$/mu.exec(block)?.[1], skill);
    assert.match(block, /^description: .+/mu);
    assert.match(block, /^license: Apache-2\.0$/mu);
    assert.ok(block.includes(`version: "${receipt.version}"`), `${skill}: version mismatch`);
    assert.ok(found.has(`skills/${skill}/agents/openai.yaml`), `${skill}: missing agent metadata`);
  }
  for (const relative of found) {
    if (!relative.startsWith("skills/") || !relative.endsWith(".md")) continue;
    const text = fs.readFileSync(path.join(root, relative), "utf8");
    for (const match of text.matchAll(/\]\(([^\s)]+)\)/gu)) {
      const target = match[1].split("#")[0];
      if (!target || /^[a-z][a-z0-9+.-]*:/iu.test(target)) continue;
      const resolved = path.resolve(root, path.dirname(relative), decodeURIComponent(target));
      // Skill resource paths can be relative to the skill root, including in references.
      const skillRoot = path.join(root, ...relative.split("/").slice(0, 2));
      const resource = path.resolve(skillRoot, decodeURIComponent(target));
      const bundled = (filename) => {
        const local = path.relative(root, filename);
        return local !== ".." && !local.startsWith(`..${path.sep}`) && !path.isAbsolute(local) && fs.existsSync(filename);
      };
      assert.ok(bundled(resolved) || bundled(resource), `broken or unbundled skill resource: ${relative} -> ${target}`);
    }
  }
  return { skills: skills.length, upstreamFiles: Object.keys(receipt.files).length };
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  try {
    const result = validatePackage(pluginRoot);
    console.log(`AI/ML API validation passed (${result.skills} skills, ${result.upstreamFiles} upstream files).`);
  } catch (error) {
    console.error(`ERROR ${error.message}`);
    process.exitCode = 1;
  }
}

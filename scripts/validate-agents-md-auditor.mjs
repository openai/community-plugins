#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const pluginName = "agents-md-auditor";
const pluginRoot = path.join(repoRoot, "plugins", pluginName);
const marketplacePath = path.join(repoRoot, ".agents", "plugins", "marketplace.json");
const safeFirstPrompt =
  "Use $audit-agents-md to audit the AGENTS.md files in this repository and explain each finding. Do not edit files yet.";
const requiredSkills = ["audit-agents-md", "revise-agents-md"];
const errors = [];

function fail(message) {
  errors.push(message);
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readJson(filePath, label) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    fail(label + " is not valid JSON: " + error.message);
    return null;
  }
}

function requireFile(root, relativePath) {
  const filePath = path.join(root, relativePath);
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    fail("missing " + relativePath);
  }
  return filePath;
}

function assertOnlyKeys(value, allowed, label) {
  if (!isObject(value)) {
    fail(label + " must be an object");
    return;
  }
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) {
      fail(label + " contains unsupported field " + key);
    }
  }
}

function requirePatterns(text, checks) {
  for (const [pattern, label] of checks) {
    if (!pattern.test(text)) {
      fail(label);
    }
  }
}

const forbiddenDirectoryNames = new Set([
  "node_modules",
  ".cache",
  "__pycache__",
  "dist",
  "build",
  "coverage",
]);
const forbiddenFileNames = new Set([".DS_Store", ".env", "npm-debug.log", "yarn-error.log"]);

function walk(root) {
  const files = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const entryPath = path.join(root, entry.name);
    const relative = path.relative(repoRoot, entryPath);
    const stat = fs.lstatSync(entryPath);
    if (stat.isSymbolicLink()) {
      fail("distribution contains symlink: " + relative);
    } else if (stat.isDirectory()) {
      if (forbiddenDirectoryNames.has(entry.name)) {
        fail("distribution contains generated or cache directory: " + relative);
      } else {
        files.push(...walk(entryPath));
      }
    } else if (!stat.isFile()) {
      fail("distribution contains non-regular file: " + relative);
    } else if (forbiddenFileNames.has(entry.name)) {
      fail("distribution contains generated or cache file: " + relative);
    } else {
      files.push(entryPath);
    }
  }
  return files;
}

for (const relativePath of [
  ".codex-plugin/plugin.json",
  "README.md",
  "LICENSE",
  "assets/icon.svg",
  "scripts/agents-md-audit.mjs",
  "fixtures/synthetic/README.md",
  "fixtures/synthetic/healthy/AGENTS.md",
  "fixtures/synthetic/stale/AGENTS.md",
  "fixtures/synthetic/monorepo/AGENTS.md",
]) {
  requireFile(pluginRoot, relativePath);
}
requireFile(repoRoot, ".github/workflows/agents-md-auditor.yml");
requireFile(repoRoot, "tests/agents-md-auditor/README.md");

const manifest = readJson(path.join(pluginRoot, ".codex-plugin", "plugin.json"), "plugin manifest");
if (manifest) {
  assertOnlyKeys(
    manifest,
    new Set(["name", "version", "description", "author", "homepage", "repository", "license", "keywords", "skills", "interface"]),
    "plugin manifest",
  );
  assertOnlyKeys(manifest.author, new Set(["name", "url"]), "manifest author");
  assertOnlyKeys(
    manifest.interface,
    new Set([
      "displayName",
      "shortDescription",
      "longDescription",
      "developerName",
      "category",
      "websiteURL",
      "privacyPolicyURL",
      "termsOfServiceURL",
      "capabilities",
      "brandColor",
      "logo",
      "composerIcon",
      "defaultPrompt",
    ]),
    "manifest interface",
  );
  if (manifest.name !== pluginName) fail("manifest name must match plugin folder");
  if (manifest.version !== "0.1.0") fail("manifest version must be 0.1.0 for the first release");
  if (manifest.license !== "Apache-2.0") fail("manifest license must be Apache-2.0");
  if (manifest.skills !== "./skills/") fail("manifest must expose ./skills/");
  if (manifest.interface?.category !== "Developer Tools") fail("manifest category must be Developer Tools");
  if (JSON.stringify(manifest.interface?.capabilities) !== JSON.stringify(["Read", "Write"])) {
    fail("manifest capabilities must be exactly Read and Write");
  }
  if (manifest.interface?.defaultPrompt?.[0] !== safeFirstPrompt) {
    fail("manifest default prompt must begin with the read-only audit flow");
  }
  for (const field of ["logo", "composerIcon"]) {
    if (manifest.interface?.[field] !== "./assets/icon.svg") {
      fail("manifest " + field + " must point to ./assets/icon.svg");
    }
  }
}

for (const forbiddenPath of [".mcp.json", ".app.json", "hooks.json", "mcp", "apps", "package.json", "node_modules"]) {
  if (fs.existsSync(path.join(pluginRoot, forbiddenPath))) {
    fail("v0.1.0 must not contain " + forbiddenPath + " (the package is dependency-free)");
  }
}

const marketplace = readJson(marketplacePath, "marketplace");
const entry = marketplace?.plugins?.find((plugin) => plugin.name === pluginName);
if (!entry) {
  fail("marketplace entry is missing");
} else {
  if (entry.source?.source !== "local" || entry.source?.path !== "./plugins/" + pluginName) {
    fail("marketplace source must be local ./plugins/" + pluginName);
  }
  if (entry.policy?.installation !== "AVAILABLE") fail("marketplace installation policy must be AVAILABLE");
  if (entry.policy?.authentication !== "ON_USE") fail("marketplace authentication policy must be ON_USE");
  if (entry.category !== "Developer Tools") fail("marketplace category must be Developer Tools");
}

for (const skillName of requiredSkills) {
  const skillPath = requireFile(pluginRoot, path.join("skills", skillName, "SKILL.md"));
  const metadataPath = requireFile(pluginRoot, path.join("skills", skillName, "agents", "openai.yaml"));
  if (fs.existsSync(skillPath)) {
    const text = fs.readFileSync(skillPath, "utf8");
    const frontmatter = text.match(/^---\r?\n([\s\S]*?)\r?\n---/u);
    if (!frontmatter) {
      fail(skillName + " SKILL.md needs YAML frontmatter");
    } else {
      requirePatterns(frontmatter[1], [
        [new RegExp("^name:\\s*" + skillName + "\\s*$", "mu"), skillName + " SKILL.md name must match its folder"],
        [/^description:\s*\S.+$/mu, skillName + " SKILL.md needs a non-empty description"],
      ]);
    }
    requirePatterns(text, [[/^## Safety contract\s*$/mu, skillName + " SKILL.md needs a Safety contract section"]]);
  }
  if (fs.existsSync(metadataPath)) {
    requirePatterns(fs.readFileSync(metadataPath, "utf8"), [
      [/^interface:\s*$/mu, skillName + " metadata needs interface"],
      [/^\s{2}display_name:\s*\S.+$/mu, skillName + " metadata needs interface.display_name"],
      [/^\s{2}short_description:\s*\S.+$/mu, skillName + " metadata needs interface.short_description"],
      [/^\s{2}default_prompt:\s*\S.+$/mu, skillName + " metadata needs interface.default_prompt"],
      [/^policy:\s*$/mu, skillName + " metadata needs policy"],
      [/^\s{2}allow_implicit_invocation:\s*false\s*$/mu, skillName + " must disable implicit invocation"],
    ]);
  }
}

const auditSkillPath = path.join(pluginRoot, "skills", "audit-agents-md", "SKILL.md");
if (fs.existsSync(auditSkillPath)) {
  requirePatterns(fs.readFileSync(auditSkillPath, "utf8"), [
    [/read-only/iu, "audit skill must say it is read-only"],
    [/Do not edit/u, "audit skill must prohibit edits"],
    [/Do not run the commands/u, "audit skill must prohibit running documented commands"],
    [/scripts\/agents-md-audit\.mjs/u, "audit skill must use the bundled checker"],
  ]);
}
const reviseSkillPath = path.join(pluginRoot, "skills", "revise-agents-md", "SKILL.md");
if (fs.existsSync(reviseSkillPath)) {
  requirePatterns(fs.readFileSync(reviseSkillPath, "utf8"), [
    [/Write only to `AGENTS\.md` or `AGENTS\.override\.md`/u, "revise skill must bound writes to AGENTS files"],
    [/approved/iu, "revise skill must require approval"],
    [/Show the proposed diff first/u, "revise skill must show a diff before writing"],
  ]);
}

const readmePath = path.join(pluginRoot, "README.md");
const readme = fs.existsSync(readmePath) ? fs.readFileSync(readmePath, "utf8") : "";
requirePatterns(readme, [
  [/^## Safe first run\s*$/mu, "README is missing a safe first run section"],
  [/^## Permissions and authentication\s*$/mu, "README is missing permissions and authentication guidance"],
  [/^## Data boundaries\s*$/mu, "README is missing data-boundary guidance"],
  [/^## Failure behavior\s*$/mu, "README is missing failure behavior"],
  [/^## Limitations\s*$/mu, "README is missing a limitations section"],
  [/Do not edit files yet\./u, "README safe first run must be read-only"],
  [/\bON_USE\b/u, "README must explain ON_USE authentication"],
  [/claude-md-management/u, "README must credit the plugin that inspired it"],
]);

for (const filePath of walk(pluginRoot)) {
  const text = fs.readFileSync(filePath, "utf8");
  const relative = path.relative(repoRoot, filePath);
  if (/\/Users\/|\/home\/[a-z]|[A-Z]:\\\\Users\\\\/u.test(text)) {
    fail("distribution contains a personal path: " + relative);
  }
}

if (errors.length > 0) {
  for (const message of errors) {
    console.error("ERROR " + message);
  }
  process.exit(1);
}

console.log("AGENTS.md Auditor validation passed.");

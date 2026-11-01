#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const pluginName = "ponder";
const pluginRoot = path.join(repoRoot, "plugins", pluginName);
const marketplacePath = path.join(
  repoRoot,
  ".agents",
  "plugins",
  "marketplace.json",
);
const safeFirstPrompt =
  "Use $ponder on the bundled synthetic fixture only to inspect unit coverage, pass convergence, and ledger integrity. Do not edit files yet.";
const requiredSkills = ["ponder"];
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

function requireFile(relativePath) {
  const filePath = path.join(pluginRoot, relativePath);
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    fail("missing " + relativePath);
  }
  return filePath;
}

function requireRepoFile(relativePath) {
  const filePath = path.join(repoRoot, relativePath);
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    fail("missing " + relativePath);
  }
  return filePath;
}

function requireDirectory(relativePath) {
  const directoryPath = path.join(pluginRoot, relativePath);
  if (!fs.existsSync(directoryPath) || !fs.statSync(directoryPath).isDirectory()) {
    fail("missing directory " + relativePath);
  }
  return directoryPath;
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

function isInside(parentDir, childPath) {
  const relative = path.relative(parentDir, childPath);
  return (
    relative !== ".." &&
    !relative.startsWith(".." + path.sep) &&
    !path.isAbsolute(relative)
  );
}

const forbiddenDirectoryNames = new Set([
  "node_modules",
  ".cache",
  "__pycache__",
  ".pytest_cache",
  ".ruff_cache",
  "DerivedData",
  ".build",
  "build",
  "dist",
  "coverage",
]);
const forbiddenFileNames = new Set([
  ".DS_Store",
  ".env",
  "npm-debug.log",
  "yarn-error.log",
]);

function walk(root) {
  const files = [];
  if (!fs.existsSync(root)) {
    return files;
  }

  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const entryPath = path.join(root, entry.name);
    const stat = fs.lstatSync(entryPath);
    const relative = path.relative(repoRoot, entryPath);

    if (stat.isSymbolicLink()) {
      fail("distribution contains symlink: " + relative);
      continue;
    }

    if (stat.isDirectory()) {
      if (forbiddenDirectoryNames.has(entry.name)) {
        fail("distribution contains generated or cache directory: " + relative);
        continue;
      }
      files.push(...walk(entryPath));
      continue;
    }

    if (!stat.isFile()) {
      fail("distribution contains non-regular file: " + relative);
      continue;
    }

    if (forbiddenFileNames.has(entry.name)) {
      fail("distribution contains generated or cache file: " + relative);
      continue;
    }
    files.push(entryPath);
  }
  return files;
}

function readText(filePath) {
  return fs.readFileSync(filePath, "utf8");
}

function requirePattern(text, pattern, label) {
  if (!pattern.test(text)) {
    fail(label);
  }
}

function requireAllPatterns(text, checks) {
  for (const [pattern, label] of checks) {
    requirePattern(text, pattern, label);
  }
}

function textLike(filePath) {
  return /\.(?:md|json|jsonl|mjs|js|ts|yaml|yml|svg|txt)$/u.test(filePath);
}

for (const relativePath of [
  ".codex-plugin/plugin.json",
  "README.md",
  "LICENSE",
  "assets/icon.svg",
  "scripts/ponder.mjs",
]) {
  requireFile(relativePath);
}
requireDirectory("fixtures/synthetic/sample-audit");
requireRepoFile("scripts/validate-ponder.mjs");
const workflowPath = requireRepoFile(".github/workflows/ponder.yml");

const manifest = readJson(
  path.join(pluginRoot, ".codex-plugin", "plugin.json"),
  "plugin manifest",
);
if (manifest) {
  assertOnlyKeys(
    manifest,
    new Set([
      "name",
      "version",
      "description",
      "author",
      "homepage",
      "repository",
      "license",
      "keywords",
      "skills",
      "interface",
    ]),
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

  if (manifest.name !== pluginName) {
    fail("manifest name must match plugin folder");
  }
  if (manifest.version !== "0.1.0") {
    fail("manifest version must be 0.1.0 for the first release");
  }
  if (manifest.skills !== "./skills/") {
    fail("manifest must expose ./skills/");
  }
  if (manifest.mcpServers !== undefined || manifest.apps !== undefined) {
    fail("v0.1.0 must not declare MCP servers or apps");
  }
  if (manifest.interface?.category !== "Developer Tools") {
    fail("manifest category must be Developer Tools");
  }
  if (
    JSON.stringify(manifest.interface?.capabilities) !==
    JSON.stringify(["Read", "Write"])
  ) {
    fail("manifest capabilities must be exactly Read and Write");
  }
  if (
    !Array.isArray(manifest.interface?.defaultPrompt) ||
    manifest.interface.defaultPrompt.length === 0 ||
    manifest.interface.defaultPrompt[0] !== safeFirstPrompt
  ) {
    fail("manifest default prompt must begin with the read-only fixture inspection flow");
  }
  if (manifest.interface?.logo !== "./assets/icon.svg") {
    fail("manifest logo must point to ./assets/icon.svg");
  }
}

for (const forbiddenPath of [".mcp.json", ".app.json", "mcp", "apps"]) {
  if (fs.existsSync(path.join(pluginRoot, forbiddenPath))) {
    fail("v0.1.0 must not contain " + forbiddenPath);
  }
}

const marketplace = readJson(marketplacePath, "marketplace");
const marketplaceEntry = marketplace?.plugins?.find(
  (entry) => entry.name === pluginName,
);
if (!marketplaceEntry) {
  fail("marketplace entry is missing");
} else {
  if (marketplaceEntry.source?.source !== "local") {
    fail("marketplace source must be local");
  }
  if (marketplaceEntry.source?.path !== "./plugins/" + pluginName) {
    fail("marketplace source path is wrong");
  }
  if (marketplaceEntry.policy?.installation !== "AVAILABLE") {
    fail("marketplace installation policy must be AVAILABLE");
  }
  if (marketplaceEntry.policy?.authentication !== "ON_USE") {
    fail("marketplace authentication policy must be ON_USE");
  }
  if (marketplaceEntry.category !== "Developer Tools") {
    fail("marketplace category must be Developer Tools");
  }
}

for (const skillName of requiredSkills) {
  const skillPath = requireFile(path.join("skills", skillName, "SKILL.md"));
  const agentPath = requireFile(
    path.join("skills", skillName, "agents", "openai.yaml"),
  );

  if (fs.existsSync(skillPath)) {
    const skillText = readText(skillPath);
    const frontmatter = skillText.match(/^---\r?\n([\s\S]*?)\r?\n---/u);
    if (!frontmatter) {
      fail(skillName + " SKILL.md needs YAML frontmatter");
    } else {
      requirePattern(
        frontmatter[1],
        new RegExp("^name:\\s*" + skillName + "\\s*$", "mu"),
        skillName + " SKILL.md name must match its folder",
      );
      requirePattern(
        frontmatter[1],
        /^description:\s*\S.+$/mu,
        skillName + " SKILL.md needs a non-empty description",
      );
    }
    requireAllPatterns(skillText, [
      [/^## Safety contract$/mu, "SKILL.md needs a Safety contract section"],
      [/read-only/iu, "SKILL.md must require read-only fixture inspection first"],
      [/--session-dir/u, "SKILL.md must bound writes to --session-dir"],
    ]);
  }

  if (fs.existsSync(agentPath)) {
    const agentText = readText(agentPath);
    requireAllPatterns(agentText, [
      [/^interface:\s*$/mu, skillName + " metadata needs interface"],
      [
        /^\s{2}display_name:\s*\S.+$/mu,
        skillName + " metadata needs interface.display_name",
      ],
      [
        /^\s{2}short_description:\s*\S.+$/mu,
        skillName + " metadata needs interface.short_description",
      ],
      [
        /^\s{2}default_prompt:\s*\S.+$/mu,
        skillName + " metadata needs interface.default_prompt",
      ],
      [/^policy:\s*$/mu, skillName + " metadata needs policy"],
      [
        /^\s{2}allow_implicit_invocation:\s*false\s*$/mu,
        skillName + " must disable implicit invocation",
      ],
    ]);
  }
}

const readmePath = path.join(pluginRoot, "README.md");
const readme = fs.existsSync(readmePath) ? readText(readmePath) : "";
requireAllPatterns(readme, [
  [/^## Safe first run\s*$/mu, "README is missing a safe first run section"],
  [
    /^## .*Permissions.*authentication.*$/imu,
    "README is missing permissions and authentication guidance",
  ],
  [
    /^## .*data boundar(?:y|ies).*$/imu,
    "README is missing data-boundary guidance",
  ],
  [/^## .*Accessibility.*$/imu, "README is missing accessibility guidance"],
  [/^## Limitations\s*$/imu, "README is missing a limitations section"],
  [/\$ponder/u, "README must show the ponder skill"],
  [/Do not edit files yet\./u, "README safe first run must be read-only"],
  [/\bRead\b/u, "README must explain Read capability"],
  [/\bWrite\b/u, "README must explain Write capability"],
  [/\bON_USE\b/u, "README must explain ON_USE authentication"],
]);

if (fs.existsSync(workflowPath)) {
  const workflow = readText(workflowPath);
  requireAllPatterns(workflow, [
    [/name:\s+Ponder E2E/u, "workflow must expose one plugin-level E2E check"],
    [/npm run test:ponder/u, "workflow must invoke the plugin master test suite"],
  ]);
  const jobsText = workflow.split(/\njobs:\s*\n/u)[1] ?? "";
  const jobNames = [...jobsText.matchAll(/^  ([a-z][a-z0-9-]*):\s*$/gmu)].map(
    (match) => match[1],
  );
  if (jobNames.length !== 1 || jobNames[0] !== "ponder-e2e") {
    fail("workflow must contain exactly one ponder-e2e job");
  }
}

const pluginFiles = walk(pluginRoot);
const publicFiles = [
  path.join(repoRoot, "README.md"),
  marketplacePath,
  path.join(repoRoot, "package.json"),
  workflowPath,
].filter((file) => fs.existsSync(file) && fs.statSync(file).isFile());

const bannedContent = [
  [/\/Users\//u, "personal macOS path"],
  [/\/home\/[A-Za-z0-9._-]+\//u, "personal Linux path"],
  [/\bfile:\/\//iu, "file URL"],
  [
    /https?:\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0|[^/\s]+\.(?:internal|corp|local))(?:[:/]|$)/iu,
    "private URL",
  ],
  [/\b(?:git@|ssh:\/\/)[^\s]+/u, "private repository-style URL"],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/u, "private key"],
  [/\bgh[pousr]_[A-Za-z0-9_]{20,}\b/u, "GitHub token"],
  [/\bsk-[A-Za-z0-9_-]{20,}\b/u, "API key"],
  [/\bAKIA[0-9A-Z]{16}\b/u, "AWS access key"],
  [
    /\b(?:password|api[_-]?key|secret|token)\s*[:=]\s*["'][^"'\s]{8,}["']/iu,
    "credential assignment",
  ],
];

for (const file of pluginFiles) {
  if (textLike(file) && /[—–]/u.test(readText(file))) {
    fail("distribution scan found em or en dash in " + path.relative(repoRoot, file));
  }
}

for (const file of [...pluginFiles, ...publicFiles]) {
  if (!textLike(file)) {
    continue;
  }
  const relative = path.relative(repoRoot, file);
  const content = readText(file);
  for (const [pattern, label] of bannedContent) {
    if (pattern.test(content)) {
      fail("distribution scan found " + label + " in " + relative);
    }
  }
}

for (const file of pluginFiles.filter((candidate) => candidate.endsWith(".mjs"))) {
  const source = readText(file);
  const importPattern =
    /(?:import\s+(?:[^"'()]+?\s+from\s+)?|export\s+[^"'()]+?\s+from\s+|import\s*\()\s*["']([^"']+)["']/gu;
  for (const match of source.matchAll(importPattern)) {
    const specifier = match[1];
    if (specifier.startsWith("node:")) {
      continue;
    }
    if (!specifier.startsWith(".")) {
      fail(
        "packaged runtime must not require node_modules: " +
          path.relative(repoRoot, file) +
          " imports " +
          specifier,
      );
      continue;
    }
    const resolved = path.resolve(path.dirname(file), specifier);
    if (!isInside(pluginRoot, resolved)) {
      fail(
        "packaged runtime import escapes the plugin: " +
          path.relative(repoRoot, file) +
          " imports " +
          specifier,
      );
    }
  }
}

const testsRoot = path.join(repoRoot, "tests", pluginName);
if (!fs.existsSync(testsRoot) || !fs.statSync(testsRoot).isDirectory()) {
  fail("missing tests/" + pluginName);
} else {
  requireRepoFile(path.join("tests", pluginName, "README.md"));
  for (const group of ["unit", "integration", "security"]) {
    const groupPath = path.join(testsRoot, group);
    if (!fs.existsSync(groupPath) || !fs.statSync(groupPath).isDirectory()) {
      fail("tests/" + pluginName + " must include " + group + "/");
      continue;
    }
    if (!walk(groupPath).some((file) => file.endsWith(".test.mjs"))) {
      fail("tests/" + pluginName + "/" + group + " must contain a .test.mjs file");
    }
  }
  const packageJsonText = readText(path.join(repoRoot, "package.json"));
  for (const group of ["unit", "integration", "security"]) {
    if (!packageJsonText.includes("tests/" + pluginName + "/" + group + "/*.test.mjs")) {
      fail("test dispatch must include tests/" + pluginName + "/" + group + "/*.test.mjs");
    }
  }
}

if (errors.length > 0) {
  for (const error of errors) {
    console.error("ERROR " + error);
  }
  process.exit(1);
}

console.log("Ponder validation passed.");

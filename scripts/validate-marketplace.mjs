#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const marketplacePath = path.join(
  repoRoot,
  ".agents",
  "plugins",
  "marketplace.json",
);

const installPolicies = new Set([
  "NOT_AVAILABLE",
  "AVAILABLE",
  "INSTALLED_BY_DEFAULT",
]);
const authPolicies = new Set(["ON_INSTALL", "ON_USE"]);
const semverPattern =
  /^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;

const errors = [];

function fail(message) {
  errors.push(message);
}

function readJson(filePath, label) {
  try {
    const value = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (!isObject(value)) {
      fail(`${label} must be a JSON object`);
      return null;
    }
    return value;
  } catch (error) {
    fail(`${label} is not valid JSON: ${error.message}`);
    return null;
  }
}

function walkFiles(rootDir) {
  if (!fs.existsSync(rootDir)) {
    return [];
  }

  const results = [];
  const entries = fs.readdirSync(rootDir, { withFileTypes: true });
  for (const entry of entries) {
    const entryPath = path.join(rootDir, entry.name);
    if (entry.isDirectory()) {
      results.push(...walkFiles(entryPath));
    } else if (entry.isFile()) {
      results.push(entryPath);
    }
  }
  return results;
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isKebabName(value) {
  return typeof value === "string" && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value);
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function isHttpsUrl(value) {
  if (!isNonEmptyString(value)) {
    return false;
  }

  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

function isInside(parentDir, childPath) {
  const relative = path.relative(parentDir, childPath);
  return (
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative)
  );
}

function relativeSourcePath(sourcePath) {
  if (!isNonEmptyString(sourcePath) || !sourcePath.startsWith("./")) {
    return null;
  }

  const resolved = path.resolve(repoRoot, sourcePath);
  return isInside(repoRoot, resolved) ? resolved : null;
}

function resolveInsidePlugin(pluginDir, relativePath, label) {
  if (!isNonEmptyString(relativePath)) {
    fail(`${label} must be a non-empty relative path`);
    return null;
  }

  const resolved = path.resolve(pluginDir, relativePath);
  if (!isInside(pluginDir, resolved)) {
    fail(`${label} must stay inside the plugin folder`);
    return null;
  }

  return resolved;
}

function validateReferencedPath(pluginDir, relativePath, label, kind) {
  const resolved = resolveInsidePlugin(pluginDir, relativePath, label);
  if (!resolved) {
    return null;
  }

  if (!fs.existsSync(resolved)) {
    fail(`${label} does not exist: ${relativePath}`);
    return null;
  }

  const stats = fs.statSync(resolved);
  if (kind === "directory" && !stats.isDirectory()) {
    fail(`${label} must point to a directory: ${relativePath}`);
    return null;
  }
  if (kind === "file" && !stats.isFile()) {
    fail(`${label} must point to a file: ${relativePath}`);
    return null;
  }

  return resolved;
}

function validatePluginEntry(entry, index) {
  const label = `plugins[${index}]`;

  if (!isObject(entry)) {
    fail(`${label} must be an object`);
    return;
  }

  if (!isKebabName(entry.name)) {
    fail(`${label}.name must be kebab-case`);
  }

  if (!isNonEmptyString(entry.category)) {
    fail(`${label}.category must be a non-empty string`);
  }

  if (!isObject(entry.source)) {
    fail(`${label}.source must be an object`);
  } else {
    if (entry.source.source !== "local") {
      fail(`${label}.source.source must be "local"`);
    }

    const pluginDir = relativeSourcePath(entry.source.path);
    if (!pluginDir) {
      fail(`${label}.source.path must be a ./-prefixed path inside the repo`);
    } else {
      validatePluginDirectory(entry, pluginDir, label);
    }
  }

  if (!isObject(entry.policy)) {
    fail(`${label}.policy must be an object`);
  } else {
    if (!installPolicies.has(entry.policy.installation)) {
      fail(
        `${label}.policy.installation must be one of ${[
          ...installPolicies,
        ].join(", ")}`,
      );
    }

    if (!authPolicies.has(entry.policy.authentication)) {
      fail(
        `${label}.policy.authentication must be one of ${[
          ...authPolicies,
        ].join(", ")}`,
      );
    }
  }
}

function validatePluginDirectory(entry, pluginDir, label) {
  if (!fs.existsSync(pluginDir) || !fs.statSync(pluginDir).isDirectory()) {
    fail(`${label}.source.path does not point to a directory: ${entry.source.path}`);
    return;
  }

  if (entry.source.path !== `./plugins/${entry.name}`) {
    fail(`${label}.source.path must be "./plugins/${entry.name}"`);
  }

  const manifestPath = path.join(pluginDir, ".codex-plugin", "plugin.json");
  if (!fs.existsSync(manifestPath)) {
    fail(`${label}.source.path is missing .codex-plugin/plugin.json`);
    return;
  }

  const manifest = readJson(manifestPath, `${label} manifest`);
  if (!manifest) {
    return;
  }

  if (manifest.name !== entry.name) {
    fail(`${label}.name must match manifest name "${manifest.name}"`);
  }

  if (!isNonEmptyString(manifest.version)) {
    fail(`${label} manifest must include a non-empty string version`);
  } else if (!semverPattern.test(manifest.version)) {
    fail(`${label} manifest version must use semantic versioning`);
  }

  if (!isNonEmptyString(manifest.description)) {
    fail(`${label} manifest must include a non-empty string description`);
  }

  if (!isObject(manifest.author) || !isNonEmptyString(manifest.author.name)) {
    fail(`${label} manifest must include author.name`);
  }

  for (const [field, value] of [
    ["homepage", manifest.homepage],
    ["repository", manifest.repository],
    ["author.url", manifest.author?.url],
  ]) {
    if (value !== undefined && !isHttpsUrl(value)) {
      fail(`${label} manifest ${field} must be an HTTPS URL`);
    }
  }

  if (!isNonEmptyString(manifest.license)) {
    fail(`${label} manifest must include a non-empty license`);
  }

  const readmePath = path.join(pluginDir, "README.md");
  if (!fs.existsSync(readmePath) || !fs.statSync(readmePath).isFile()) {
    fail(`${label}.source.path is missing README.md`);
  }

  if (!isObject(manifest.interface)) {
    fail(`${label} manifest must include interface metadata`);
  } else {
    for (const field of ["displayName", "shortDescription", "category"]) {
      if (!isNonEmptyString(manifest.interface[field])) {
        fail(`${label} manifest interface.${field} must be a non-empty string`);
      }
    }

    if (manifest.interface.category !== entry.category) {
      fail(`${label} category must match manifest interface.category`);
    }

    if (
      !Array.isArray(manifest.interface.capabilities) ||
      manifest.interface.capabilities.length === 0 ||
      manifest.interface.capabilities.some((capability) => !isNonEmptyString(capability))
    ) {
      fail(`${label} manifest interface.capabilities must be a non-empty string array`);
    }

    if (
      !Array.isArray(manifest.interface.defaultPrompt) ||
      manifest.interface.defaultPrompt.length === 0 ||
      manifest.interface.defaultPrompt.some((prompt) => !isNonEmptyString(prompt))
    ) {
      fail(`${label} manifest interface.defaultPrompt must be a non-empty string array`);
    }

    for (const field of ["websiteURL", "privacyPolicyURL", "termsOfServiceURL"]) {
      const value = manifest.interface[field];
      if (value !== undefined && !isHttpsUrl(value)) {
        fail(`${label} manifest interface.${field} must be an HTTPS URL`);
      }
    }

    for (const field of ["logo", "composerIcon"]) {
      if (manifest.interface[field] !== undefined) {
        validateReferencedPath(
          pluginDir,
          manifest.interface[field],
          `${label} manifest interface.${field}`,
          "file",
        );
      }
    }
  }

  if (manifest.skills) {
    const skillsDir = validateReferencedPath(
      pluginDir,
      manifest.skills,
      `${label} manifest skills path`,
      "directory",
    );
    if (skillsDir) {
      validateAgentMetadataFiles(skillsDir, label);
    }
  }

  for (const field of ["mcpServers", "apps"]) {
    if (manifest[field]) {
      validateReferencedPath(
        pluginDir,
        manifest[field],
        `${label} manifest ${field} path`,
        "file",
      );
    }
  }
}

function validateAgentMetadataFiles(skillsDir, label) {
  for (const filePath of walkFiles(skillsDir)) {
    if (path.basename(filePath) !== "openai.yaml") {
      continue;
    }

    if (path.basename(path.dirname(filePath)) !== "agents") {
      continue;
    }

    const relativePath = path.relative(repoRoot, filePath);
    const content = fs.readFileSync(filePath, "utf8");
    const lines = content.split(/\r?\n/);
    const hasInterface = lines.some((line) => line.trim() === "interface:");
    if (!hasInterface) {
      fail(`${label} ${relativePath} must include an interface wrapper`);
    }

    for (const key of ["display_name", "short_description", "default_prompt"]) {
      if (lines.some((line) => line.startsWith(`${key}:`))) {
        fail(`${label} ${relativePath} must nest ${key} under interface`);
      }
    }
  }
}

const marketplace = readJson(marketplacePath, "marketplace");

if (marketplace) {
  if (marketplace.name !== "community-plugins") {
    fail('marketplace.name must be "community-plugins"');
  }

  if (!isObject(marketplace.interface)) {
    fail("marketplace.interface must be an object");
  } else if (marketplace.interface.displayName !== "Community Plugins") {
    fail('marketplace.interface.displayName must be "Community Plugins"');
  }

  if (!Array.isArray(marketplace.plugins)) {
    fail("marketplace.plugins must be an array");
  } else {
    const seenNames = new Set();
    const seenPaths = new Set();
    for (const [index, entry] of marketplace.plugins.entries()) {
      if (isObject(entry) && seenNames.has(entry.name)) {
        fail(`plugins[${index}].name duplicates "${entry.name}"`);
      }
      if (isObject(entry) && seenPaths.has(entry.source?.path)) {
        fail(`plugins[${index}].source.path duplicates "${entry.source?.path}"`);
      }
      if (isObject(entry)) {
        seenNames.add(entry.name);
        seenPaths.add(entry.source?.path);
      }
      validatePluginEntry(entry, index);
    }

    if (marketplace.plugins.length === 0) {
      fail("marketplace must include at least one plugin");
    }
  }
}

if (errors.length > 0) {
  for (const message of errors) {
    console.error(`ERROR ${message}`);
  }
  process.exit(1);
}

console.log("Marketplace validation passed.");

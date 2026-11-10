#!/usr/bin/env node

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { lstatSync, readFileSync, readdirSync, realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repositoryRoot = fileURLToPath(new URL("../", import.meta.url));
export const GRAFANA_PLUGIN_ROOT = join(repositoryRoot, "plugins/grafana-observability");
export const GRAFANA_ENTRYPOINTS = Object.freeze([
  "mcp/server.mjs",
  "workers/provider-worker.mjs",
  "scripts/observabilityctl.mjs",
  "scripts/grafana-authorityctl.mjs",
]);
export const GRAFANA_PACK_IDS = Object.freeze([
  "grafana.infrastructure", "grafana.apm", "grafana.logs", "grafana.iot-edge", "grafana.business-kpis",
]);
export const GRAFANA_LAUNCH_ENV_VARS = Object.freeze([
  "PRODUCTION_MONITORING_MODE",
  "PRODUCTION_MONITORING_TRUST_ROOT",
  "PRODUCTION_MONITORING_ACTIVATION_RECEIPT",
  "PRODUCTION_MONITORING_ARTIFACT_ROOT",
  "PRODUCTION_MONITORING_STATE_ROOT",
]);
const SKILLS = [
  "analyze-log-signals", "coordinate-grafana-incident", "inspect-infrastructure-health", "investigate-application-performance",
  "monitor-iot-edge", "review-business-kpis", "review-grafana-incident", "setup-grafana-observability",
];
const SHA256 = /^[a-f0-9]{64}$/u;
const NATIVE_SHA256 = "9627560bd2490b8495504df6b48dafe0153883aab09fd8eee976f826c74a6fff";

export function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function inside(root, name) {
  assert.equal(typeof name, "string");
  const target = resolve(root, name);
  const rel = relative(root, target);
  assert.ok(rel !== ".." && !rel.startsWith(`..${sep}`) && !isAbsolute(rel), `Path escapes plugin: ${name}`);
  return target;
}

function regularFile(root, name) {
  const target = inside(root, name);
  assert.ok(lstatSync(target).isFile(), `Not a regular file: ${name}`);
  const real = realpathSync(target);
  assert.equal(real, target, `Symlinked package path: ${name}`);
  return target;
}

export function verifySourceLoader(pluginRoot, entrypoint) {
  const loader = readFileSync(regularFile(pluginRoot, entrypoint), "utf8");
  const match = loader.match(/^const __PM_SOURCE_PARTS__=(\{[^\n]+\});$/mu);
  assert.ok(match, `Missing source integrity manifest: ${entrypoint}`);
  const manifest = JSON.parse(match[1]);
  assert.equal(manifest.schemaVersion, 1);
  assert.ok(Number.isSafeInteger(manifest.sourceBytes) && manifest.sourceBytes > 0);
  assert.match(manifest.sourceSha256, SHA256);
  assert.ok(Array.isArray(manifest.parts) && manifest.parts.length > 0);
  const directory = dirname(inside(pluginRoot, entrypoint));
  const prefix = `${basename(entrypoint)}.source.part-`;
  const parts = manifest.parts.map((part, index) => {
    assert.equal(part.name, `${prefix}${String(index).padStart(3, "0")}`);
    assert.ok(Number.isSafeInteger(part.bytes) && part.bytes > 0);
    assert.match(part.sha256, SHA256);
    const bytes = readFileSync(regularFile(pluginRoot, join(dirname(entrypoint), part.name)));
    assert.equal(bytes.byteLength, part.bytes, `Wrong part size: ${entrypoint}/${part.name}`);
    assert.equal(sha256(bytes), part.sha256, `Wrong part digest: ${entrypoint}/${part.name}`);
    return bytes;
  });
  assert.deepEqual(
    readdirSync(directory).filter((name) => name.startsWith(prefix)).sort(),
    manifest.parts.map((part) => part.name).sort(),
    `Missing or unlisted source parts: ${entrypoint}`,
  );
  const source = Buffer.concat(parts);
  assert.equal(source.byteLength, manifest.sourceBytes, `Wrong complete size: ${entrypoint}`);
  assert.equal(sha256(source), manifest.sourceSha256, `Wrong complete digest: ${entrypoint}`);
  return { manifest, source };
}

function walk(root, visit) {
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const target = join(root, entry.name);
    assert.equal(entry.isSymbolicLink(), false, `Symlink in package: ${target}`);
    assert.notEqual(entry.name, "node_modules", "Packaged dependencies must be bundled, not a node_modules tree");
    if (entry.isDirectory()) walk(target, visit);
    else {
      assert.ok(entry.isFile(), `Non-file package member: ${target}`);
      visit(target);
    }
  }
}

function resolvePointer(schema, fragment, label) {
  if (fragment === "") return;
  assert.ok(fragment.startsWith("/"), `Unsupported schema anchor: ${label}`);
  let current = schema;
  for (const raw of fragment.slice(1).split("/")) {
    const key = decodeURIComponent(raw).replaceAll("~1", "/").replaceAll("~0", "~");
    assert.ok(current && typeof current === "object" && Object.hasOwn(current, key), `Unresolved schema reference: ${label}`);
    current = current[key];
  }
}

function validateSchemas(pluginRoot) {
  const schemaRoot = join(pluginRoot, "schemas");
  const byId = new Map();
  for (const name of readdirSync(schemaRoot).filter((item) => item.endsWith(".schema.json"))) {
    const schema = JSON.parse(readFileSync(regularFile(pluginRoot, `schemas/${name}`), "utf8"));
    assert.equal(typeof schema.$id, "string", `Missing schema ID: ${name}`);
    assert.equal(byId.has(schema.$id), false, `Duplicate schema ID: ${schema.$id}`);
    byId.set(schema.$id, schema);
  }
  function visit(value, schema) {
    if (!value || typeof value !== "object") return;
    if (typeof value.$ref === "string") {
      const [id, fragment = ""] = value.$ref.split("#");
      const referenced = id === "" ? schema : byId.get(id);
      assert.ok(referenced, `Missing bundled schema: ${value.$ref}`);
      resolvePointer(referenced, fragment, value.$ref);
    }
    for (const child of Object.values(value)) visit(child, schema);
  }
  for (const schema of byId.values()) visit(schema, schema);
  return byId.size;
}

export function validateGrafanaPlugin(pluginRoot = GRAFANA_PLUGIN_ROOT) {
  pluginRoot = realpathSync(pluginRoot);
  let fileCount = 0;
  walk(pluginRoot, () => { fileCount += 1; });
  const manifest = JSON.parse(readFileSync(regularFile(pluginRoot, ".codex-plugin/plugin.json"), "utf8"));
  assert.equal(manifest.name, "grafana-observability");
  assert.match(manifest.version, /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/u);
  assert.equal(manifest.skills, "./skills/");
  assert.equal(manifest.mcpServers, "./.mcp.json");
  assert.equal(manifest.license, "Apache-2.0");
  assert.doesNotMatch(JSON.stringify(manifest), /\[TODO:/u);
  for (const key of ["logo", "composerIcon", "logoDark"]) {
    if (manifest.interface[key]) regularFile(pluginRoot, manifest.interface[key]);
  }
  const mcp = JSON.parse(readFileSync(regularFile(pluginRoot, manifest.mcpServers), "utf8"));
  assert.deepEqual(Object.keys(mcp.mcpServers), ["grafana-observability"]);
  const launch = mcp.mcpServers[manifest.name];
  assert.equal(launch.command, "node");
  assert.deepEqual(launch.args, ["./mcp/server.mjs", "--stdio"]);
  assert.equal(launch.cwd, ".");
  assert.deepEqual(launch.env_vars, [...GRAFANA_LAUNCH_ENV_VARS], "Launcher must forward only the reviewed non-secret settings");
  assert.equal(Object.hasOwn(launch, "env"), false, "Do not embed environment values in the distributed launcher");
  assert.ok(launch.tool_timeout_sec >= 3600, "Launcher must admit the bounded current-plus-baseline run");
  const sourceDigests = {};
  for (const entrypoint of GRAFANA_ENTRYPOINTS) {
    sourceDigests[entrypoint] = verifySourceLoader(pluginRoot, entrypoint).manifest.sourceSha256;
  }
  const native = readFileSync(regularFile(pluginRoot, "native/keyring.darwin-arm64.node"));
  assert.equal(native.byteLength, 491232, "Unexpected native keyring asset size");
  assert.equal(sha256(native), NATIVE_SHA256, "Native keyring asset failed release pin");
  const actualSkills = readdirSync(join(pluginRoot, "skills"), { withFileTypes: true })
    .filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort();
  assert.deepEqual(actualSkills, [...SKILLS].sort());
  for (const skill of SKILLS) {
    const text = readFileSync(regularFile(pluginRoot, `skills/${skill}/SKILL.md`), "utf8");
    assert.ok(text.startsWith("---\n"), `Missing skill frontmatter: ${skill}`);
    assert.match(text, new RegExp(`^name: ${skill}$`, "m"));
    regularFile(pluginRoot, `skills/${skill}/agents/openai.yaml`);
  }
  regularFile(pluginRoot, "LICENSE");
  regularFile(pluginRoot, "THIRD_PARTY_NOTICES.txt");
  const schemaCount = validateSchemas(pluginRoot);
  return { fileCount, schemaCount, sourceDigests, nativeSha256: NATIVE_SHA256 };
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try {
    const result = validateGrafanaPlugin(process.argv[2]);
    console.log(`Grafana package validation passed (${result.fileCount} files, ${result.schemaCount} schemas, ${GRAFANA_ENTRYPOINTS.length} verified entrypoints).`);
  } catch (error) {
    console.error(`Grafana package validation failed: ${error.message}`);
    process.exitCode = 1;
  }
}

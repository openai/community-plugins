import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { chmodSync, cpSync, createWriteStream, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import test from "node:test";

import { REPOSITORY_ROOT, SOURCE_PLUGIN_ROOT } from "./helpers/mcp-client.mjs";

// Official OpenAI release digests, pinned to the locally reviewed client version:
// https://github.com/openai/codex/releases/tag/rust-v0.144.3
const RELEASES = Object.freeze({
  aarch64: {
    filename: "codex-aarch64-unknown-linux-musl",
    sha256: "dd76cfd5a2cf9bcf0e3224afe28e23065cfd27262e06e0ffbc8fa40343f0905a",
  },
  x86_64: {
    filename: "codex-x86_64-unknown-linux-musl",
    sha256: "b9b4ae8e9b561c64dfbc5ef52c6319cba750ac87de3c7f55885026231e3aea89",
  },
});
const IMAGE = "node:22.19.0-alpine3.22";
const VERSION = "0.144.3";
const companionFiles = [
  "tests/grafana-observability/support/installed-client-check.mjs",
  "tests/grafana-observability/support/synthetic-grafana.mjs",
  "tests/grafana-observability/support/authority-definition.json",
  "tests/grafana-observability/helpers/synthetic-session.mjs",
  "tests/grafana-observability/helpers/live-authority.mjs",
  "tests/grafana-observability/helpers/mcp-client.mjs",
  "scripts/validate-grafana.mjs",
];

function sync(command, args, env) {
  const result = spawnSync(command, args, { env, encoding: "utf8", timeout: 20_000, maxBuffer: 2 * 1024 * 1024 });
  assert.equal(result.error, undefined, result.error?.message);
  assert.equal(result.status, 0, `${command} failed: ${result.stderr}`);
  return result.stdout.trim();
}

function dockerEnvironment() {
  for (const name of ["DOCKER_HOST", "DOCKER_CONTEXT"]) assert.ok(!process.env[name], `${name} must be unset`);
  const env = {};
  for (const name of ["PATH", "HOME", "USERPROFILE", "SystemRoot", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP"]) {
    if (process.env[name] !== undefined) env[name] = process.env[name];
  }
  const context = sync("docker", ["context", "show"], env);
  const endpoint = JSON.parse(sync("docker", ["context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"], env));
  assert.match(endpoint, /^(?:unix:\/\/\/|npipe:\/\/)/u, "Installed-client tests require a local Docker daemon");
  return { ...env, DOCKER_CONTEXT: context };
}

function run(command, args, { env, timeoutMs = 120_000 } = {}) {
  return new Promise((accept, reject) => {
    const child = spawn(command, args, { env, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let force;
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout = `${stdout}${chunk}`.slice(-262_144); });
    child.stderr.on("data", (chunk) => { stderr = `${stderr}${chunk}`.slice(-65_536); });
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
      force = setTimeout(() => child.kill("SIGKILL"), 2_000);
    }, timeoutMs);
    child.once("error", (error) => { clearTimeout(timer); clearTimeout(force); reject(error); });
    child.once("close", (code, signal) => {
      clearTimeout(timer); clearTimeout(force);
      if (timedOut || code !== 0) reject(new Error(`${command} ${timedOut ? "timed out" : "failed"} (${code ?? signal}): ${stderr}\n${stdout}`));
      else accept(stdout.trim());
    });
  });
}

// Container root has no DAC capabilities. On native Linux it cannot traverse a
// nonroot runner's 0700 bind mount. Normalize only these fresh, public package
// copies, never their private parent, the checkout, or any host configuration.
function makeOwnedMountReadable(root) {
  const stat = lstatSync(root);
  assert.equal(stat.isSymbolicLink(), false, "Installed-client inputs must not contain symlinks");
  if (stat.isDirectory()) {
    chmodSync(root, 0o755);
    for (const entry of readdirSync(root)) makeOwnedMountReadable(join(root, entry));
    assert.equal(lstatSync(root).mode & 0o777, 0o755);
  } else {
    assert.ok(stat.isFile(), "Installed-client inputs must be regular files or directories");
    chmodSync(root, 0o644);
    assert.equal(lstatSync(root).mode & 0o777, 0o644);
  }
}

async function stageOfficialClient(root, architecture) {
  const release = RELEASES[architecture];
  assert.ok(release, `Unsupported local Docker architecture: ${architecture}`);
  const cache = join(homedir(), ".cache/community-grafana-codex", VERSION);
  mkdirSync(cache, { recursive: true, mode: 0o700 });
  const archive = join(cache, `${release.filename}.tar.gz`);
  if (!existsSync(archive)) {
    const partial = join(root, "codex-download.tar.gz");
    const url = `https://github.com/openai/codex/releases/download/rust-v${VERSION}/${release.filename}.tar.gz`;
    const response = await fetch(url, { signal: AbortSignal.timeout(180_000) });
    assert.equal(response.status, 200, "Unable to download the pinned official Codex release");
    await pipeline(Readable.fromWeb(response.body), createWriteStream(partial, { flags: "wx", mode: 0o600 }));
    assert.equal(createHash("sha256").update(readFileSync(partial)).digest("hex"), release.sha256, "Codex release digest mismatch");
    renameSync(partial, archive);
  }
  assert.equal(createHash("sha256").update(readFileSync(archive)).digest("hex"), release.sha256, "Cached Codex release digest mismatch");
  assert.equal(sync("tar", ["-tzf", archive]), release.filename, "Unexpected files in the pinned Codex archive");
  const binaryRoot = join(root, "codex");
  mkdirSync(binaryRoot, { mode: 0o700 });
  sync("tar", ["-xzf", archive, "--no-same-owner", "-C", binaryRoot]);
  renameSync(join(binaryRoot, release.filename), join(binaryRoot, "codex"));
  return binaryRoot;
}

test("actual Codex installs eight skills, rejects inactive collection, and collects synthetic evidence through its five-tool MCP", {
  skip: process.env.GRAFANA_E2E_INSTALLED_CLIENT !== "1" && "Set GRAFANA_E2E_INSTALLED_CLIENT=1 for the disposable actual Codex client test",
  timeout: 480_000,
}, async (t) => {
  // Desktop Docker/Colima shares the real user directory, but not necessarily
  // macOS's private per-process temp directory. This is task-owned cache staging;
  // neither the host home nor its Codex configuration is mounted in the container.
  const stagingParent = join(homedir(), ".cache/community-grafana-codex");
  mkdirSync(stagingParent, { recursive: true, mode: 0o700 });
  const stagingRoot = realpathSync(mkdtempSync(join(stagingParent, "installed-e2e-")));
  t.after(() => rmSync(stagingRoot, { recursive: true, force: true }));
  const env = dockerEnvironment();
  const reportedArchitecture = sync("docker", ["info", "--format", "{{.Architecture}}"], env);
  const architecture = ({ arm64: "aarch64", amd64: "x86_64" })[reportedArchitecture] ?? reportedArchitecture;
  const binaryRoot = await stageOfficialClient(stagingRoot, architecture);
  const input = join(stagingRoot, "input");
  const marketplace = join(input, "marketplace");
  const manifestRoot = join(marketplace, ".agents/plugins");
  mkdirSync(manifestRoot, { recursive: true, mode: 0o700 });
  const manifest = JSON.parse(readFileSync(join(REPOSITORY_ROOT, ".agents/plugins/marketplace.json"), "utf8"));
  const entries = manifest.plugins.filter((entry) => entry.name === "grafana-observability");
  assert.equal(entries.length, 1);
  writeFileSync(join(manifestRoot, "marketplace.json"), `${JSON.stringify({ ...manifest, plugins: entries }, null, 2)}\n`, { mode: 0o600 });
  cpSync(SOURCE_PLUGIN_ROOT, join(marketplace, "plugins/grafana-observability"), { recursive: true, dereference: false });
  for (const file of companionFiles) {
    const destination = join(input, "companion", file);
    mkdirSync(join(destination, ".."), { recursive: true, mode: 0o700 });
    cpSync(join(REPOSITORY_ROOT, file), destination, { dereference: false });
  }
  makeOwnedMountReadable(input);
  makeOwnedMountReadable(binaryRoot);
  chmodSync(join(binaryRoot, "codex"), 0o755);
  assert.equal(lstatSync(join(binaryRoot, "codex")).mode & 0o777, 0o755);
  assert.equal(lstatSync(stagingRoot).mode & 0o777, 0o700, "The outer task-owned staging directory must remain private");
  const image = spawnSync("docker", ["image", "inspect", IMAGE], { env, stdio: "ignore", timeout: 15_000 });
  if (image.status !== 0) await run("docker", ["pull", IMAGE], { env, timeoutMs: 180_000 });
  for (const launcher of ["plugin", "standalone-config"]) {
    const name = `community-grafana-installed-client-${randomUUID().slice(0, 8)}`;
    try {
      const stdout = await run("docker", [
        "run", "--name", name, "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "128",
        "--tmpfs", "/root:rw,exec,nosuid,nodev,mode=0700", "--tmpfs", "/tmp:rw,exec,nosuid,nodev,mode=1777",
        "--mount", `type=bind,src=${binaryRoot},dst=/opt/codex,readonly`,
        "--mount", `type=bind,src=${input},dst=/input,readonly`,
        IMAGE, "node", "/input/companion/tests/grafana-observability/support/installed-client-check.mjs", launcher,
      ], { env, timeoutMs: 240_000 });
      const proof = JSON.parse(stdout.split("\n").at(-1));
      assert.equal(proof.codexVersion, "codex-cli 0.144.3");
      assert.equal(proof.launcher, launcher);
      assert.equal(proof.taskScopedStartup, "ready");
      assert.equal(proof.network, "none");
      assert.equal(proof.modelTurnsSubmitted, 0);
      assert.equal(proof.hostConfigurationMounted, false);
      assert.equal(proof.tools.length, 5);
      assert.equal(proof.fixtureProfile, "COMPLETE");
      assert.equal(proof.fixturePacks, "EMPTY");
      assert.equal(proof.inactiveCollection, "PACK_NOT_ACTIVE");
      assert.equal(proof.skills.length, 8);
      assert.ok(proof.skills.includes("grafana-observability:coordinate-grafana-incident"));
      assert.ok(proof.skills.includes("grafana-observability:review-grafana-incident"));
      assert.equal(proof.installedBytesPreserved, true);
      assert.equal(proof.synthetic.origin, "SIMULATED");
      assert.equal(proof.synthetic.profileStatus, "PARTIAL");
      assert.equal(proof.synthetic.readiness, "WARN");
      assert.equal(proof.synthetic.reportStatus, "COMPLETE");
      assert.equal(proof.synthetic.providerReads, 10);
      assert.equal(proof.synthetic.evidenceCount, 10);
      assert.equal(proof.synthetic.sourceLinkCount, 10);
      assert.equal(proof.synthetic.ownedReferences, true);
      assert.equal(proof.synthetic.unexpectedRequests, 0);
      assert.deepEqual(proof.synthetic.cleanup, { temporaryStateRemoved: true, mcpClosed: true, providerClosed: true });
      assert.equal(proof.realGrafana, false);
      assert.equal(proof.diagnosis, "NOT_ESTABLISHED");
      assert.equal(proof.recovery, "NOT_ASSESSED");
      t.diagnostic(JSON.stringify(proof));
    } finally {
      // Address only this random task-owned name; never prune or remove others.
      const names = sync("docker", ["container", "ls", "--all", "--filter", `name=^${name}$`, "--format", "{{.Names}}"], env);
      if (names === name) await run("docker", ["rm", "--force", name], { env, timeoutMs: 30_000 });
      else assert.equal(names, "", "Unexpected container matched the exact task-owned name");
    }
  }
});

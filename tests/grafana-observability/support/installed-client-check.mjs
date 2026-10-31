// Runs only inside the disposable, network-disabled installed-client container.
// No model turn is submitted. The installer, app-server, and MCP launcher are real.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, readlinkSync, realpathSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, relative } from "node:path";

import { collectSyntheticIncident, startSyntheticSession, syntheticWindow } from "../helpers/synthetic-session.mjs";

const codex = "/opt/codex/codex";
const marketplace = "/input/marketplace";
const pluginName = "grafana-observability";
const pluginId = `${pluginName}@community-plugins`;
const launcher = process.argv[2] ?? "plugin";
assert.ok(["plugin", "standalone-config"].includes(launcher));
const standaloneName = "grafana-observability-local";
const workspace = "/tmp/installed-client-workspace";
const expectedEnvironment = Object.freeze({
  PRODUCTION_MONITORING_MODE: "fixture",
  PRODUCTION_MONITORING_TRUST_ROOT: "/tmp/installed-client-authority",
  PRODUCTION_MONITORING_ACTIVATION_RECEIPT: "/tmp/installed-client-receipt.json",
  PRODUCTION_MONITORING_ARTIFACT_ROOT: "/tmp/installed-client-artifacts",
  PRODUCTION_MONITORING_STATE_ROOT: "/tmp/installed-client-state",
});
const expectedTools = [
  "grafana_get_evidence", "grafana_list_packs", "grafana_profile_status",
  "grafana_resolve_link", "grafana_run_pack",
];
const expectedSkills = [
  "analyze-log-signals", "coordinate-grafana-incident", "inspect-infrastructure-health",
  "investigate-application-performance", "monitor-iot-edge", "review-business-kpis",
  "review-grafana-incident", "setup-grafana-observability",
];
const delay = (milliseconds) => new Promise((accept) => setTimeout(accept, milliseconds));

function command(args) {
  const result = spawnSync(codex, args, {
    encoding: "utf8", timeout: 60_000, maxBuffer: 2 * 1024 * 1024,
  });
  assert.equal(result.error, undefined, result.error?.message);
  assert.equal(result.status, 0, `Codex ${args[0]} failed: ${result.stderr}`);
  return result.stdout.trim();
}

function treeHashes(root) {
  const rows = [];
  function visit(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const filename = join(directory, entry.name);
      assert.ok(!entry.isSymbolicLink(), "The installed package must contain no symlinks");
      if (entry.isDirectory()) visit(filename);
      else {
        assert.ok(entry.isFile());
        rows.push([relative(root, filename), createHash("sha256").update(readFileSync(filename)).digest("hex")]);
      }
    }
  }
  visit(root);
  return rows.sort(([a], [b]) => a.localeCompare(b));
}

class AppServer {
  pending = new Map();
  notifications = [];
  nextId = 1;
  stderr = "";
  buffer = "";
  closed = false;
  resourceSamples = [];

  constructor(environment) {
    this.child = spawn(codex, ["app-server", "--listen", "stdio://"], {
      cwd: workspace,
      // These values are test-owned, nonsecret sentinels. No credentials exist in
      // this container, including the deliberately unallowlisted token sentinel.
      env: {
        ...process.env, ...(launcher === "plugin" ? environment : {}),
        RUST_LOG: "codex_mcp=debug,codex_rmcp_client=debug",
        NODE_OPTIONS: "--no-warnings",
        GRAFANA_E2E_DISPOSABLE_BEARER: "not-a-credential-installed-client-sentinel",
        INSTALLED_CLIENT_UNALLOWLISTED: "must-not-reach-mcp",
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.child.stdout.setEncoding("utf8");
    this.child.stderr.setEncoding("utf8");
    this.child.stderr.on("data", (chunk) => { this.stderr = `${this.stderr}${chunk}`.slice(-65_536); });
    this.child.stdout.on("data", (chunk) => this.receive(chunk));
    this.child.on("error", (error) => this.fail(error));
    this.child.stdin.on("error", (error) => this.fail(error));
    this.completion = new Promise((accept) => {
      this.child.once("close", (code, signal) => {
        this.closed = true;
        this.fail(new Error(`Codex app-server closed (${code ?? signal})`));
        accept({ code, signal });
      });
    });
  }

  fail(error) {
    for (const { reject, timer } of this.pending.values()) { clearTimeout(timer); reject(error); }
    this.pending.clear();
  }

  receive(chunk) {
    this.buffer += chunk;
    if (this.buffer.length > 8 * 1024 * 1024) {
      this.fail(new Error("Codex app-server response exceeded the test bound"));
      this.child.kill();
      return;
    }
    let newline;
    while ((newline = this.buffer.indexOf("\n")) !== -1) {
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      if (!line) continue;
      let message;
      try { message = JSON.parse(line); }
      catch { this.fail(new Error("Codex app-server emitted non-JSON stdout")); return; }
      const pending = this.pending.get(message.id);
      if (pending) {
        clearTimeout(pending.timer);
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(`Codex ${pending.method}: ${message.error.message}`));
        else pending.accept(message.result);
      } else if (message.method && Object.hasOwn(message, "id")) {
        // This qualification never approves unexpected client-side actions.
        this.child.stdin.write(`${JSON.stringify({ id: message.id, error: { code: -32601, message: "Unsupported by installed-client qualification" } })}\n`);
      } else this.notifications.push(message);
    }
  }

  request(method, params = {}) {
    assert.ok(["initialize", "thread/start", "mcpServerStatus/list", "mcpServer/tool/call", "skills/list"].includes(method));
    assert.ok(!this.closed);
    const id = this.nextId++;
    this.sampleResources(`before:${method}`);
    return new Promise((accept, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`Codex request timed out: ${method}`)); }, 60_000);
      this.pending.set(id, { method, accept, reject, timer });
      this.child.stdin.write(`${JSON.stringify({ id, method, params })}\n`);
    });
  }

  notify(method, params = {}) {
    this.child.stdin.write(`${JSON.stringify({ method, params })}\n`);
  }

  async waitForMcpStartup(threadId, name) {
    const deadline = Date.now() + 45_000;
    do {
      const updates = this.notifications.filter((entry) => (
        entry.method === "mcpServer/startupStatus/updated"
        && entry.params?.threadId === threadId
        && entry.params?.name === name
      ));
      const latest = updates.at(-1)?.params;
      if (latest?.status === "ready") return latest;
      if (latest?.status === "failed" || latest?.status === "cancelled") {
        throw new Error(`Task-scoped MCP startup ${latest.status}: ${latest.error ?? latest.failureReason ?? name}`);
      }
      if (this.closed) throw new Error("Codex app-server closed before task-scoped MCP startup");
      await delay(20);
    } while (Date.now() < deadline);
    throw new Error("Task-scoped MCP startup notification timed out");
  }

  sampleResources(stage) {
    const read = (path) => { try { return readFileSync(path, "utf8").trim().slice(0, 1_024); } catch { return "unavailable"; } };
    this.resourceSamples.push({
      stage,
      pidsCurrent: read("/sys/fs/cgroup/pids.current"),
      pidsPeak: read("/sys/fs/cgroup/pids.peak"),
      pidsMax: read("/sys/fs/cgroup/pids.max"),
      pidsEvents: read("/sys/fs/cgroup/pids.events"),
      memoryEvents: read("/sys/fs/cgroup/memory.events"),
    });
    this.resourceSamples = this.resourceSamples.slice(-16);
  }

  diagnosticSummary() {
    this.sampleResources("diagnostic");
    // This container has no credentials or host configuration. Still redact
    // token-shaped text, and include only bounded MCP lifecycle diagnostics.
    const redact = (value) => String(value)
      .replaceAll(/glsa_[A-Za-z0-9_=-]+/gu, "[redacted]")
      .replaceAll(/(Bearer|Basic)\s+[A-Za-z0-9._~+\/-]+=*/gu, "$1 [redacted]")
      .replaceAll("not-a-credential-installed-client-sentinel", "[test sentinel]");
    return {
      launcher,
      resources: this.resourceSamples,
      appServerStderr: redact(this.stderr).slice(-16_384),
      mcpNotifications: this.notifications.filter((entry) => /mcp/iu.test(entry.method ?? "")).slice(-12).map((entry) => redact(JSON.stringify(entry)).slice(0, 2_048)),
    };
  }

  async close() {
    if (this.closed) return this.completion;
    this.child.stdin.end();
    let timer;
    let force;
    try {
      timer = setTimeout(() => {
        this.child.kill("SIGTERM");
        force = setTimeout(() => this.child.kill("SIGKILL"), 2_000);
      }, 5_000);
      return await this.completion;
    } finally { clearTimeout(timer); clearTimeout(force); }
  }
}

function inspectOwnedMcpProcess(installedRoot, environment) {
  const processes = [];
  for (const name of readdirSync("/proc")) {
    if (!/^[0-9]+$/u.test(name)) continue;
    try {
      const args = readFileSync(`/proc/${name}/cmdline`, "utf8").split("\0").filter(Boolean);
      if (!args.includes("./mcp/server.mjs")) continue;
      const cwd = realpathSync(readlinkSync(`/proc/${name}/cwd`));
      const entries = readFileSync(`/proc/${name}/environ`, "utf8").split("\0").filter(Boolean);
      const environment = Object.fromEntries(entries.map((entry) => {
        const separator = entry.indexOf("=");
        return [entry.slice(0, separator), entry.slice(separator + 1)];
      }));
      processes.push({ args, cwd, environment });
    } catch (error) {
      if (!["ENOENT", "ESRCH"].includes(error.code)) throw error;
    }
  }
  assert.equal(processes.length, 1, "Exactly one installed Grafana MCP process must be running");
  const processInfo = processes[0];
  assert.equal(processInfo.cwd, realpathSync(installedRoot), "Codex did not resolve cwd relative to the installed plugin");
  assert.deepEqual(processInfo.args.slice(1), ["./mcp/server.mjs", "--stdio"]);
  for (const [name, value] of Object.entries(environment)) {
    assert.equal(processInfo.environment[name], value, `Installed MCP did not receive the allowlisted ${name}`);
  }
  for (const name of ["NODE_OPTIONS", "GRAFANA_E2E_DISPOSABLE_BEARER", "INSTALLED_CLIENT_UNALLOWLISTED"]) {
    assert.equal(Object.hasOwn(processInfo.environment, name), false, `Installed MCP inherited unallowlisted ${name}`);
  }
  return { cwd: processInfo.cwd, args: processInfo.args.slice(1), forwardedEnvironmentNames: Object.keys(environment) };
}

function toolData(result) {
  assert.notEqual(result.isError, true, JSON.stringify(result));
  return result.structuredContent ?? JSON.parse(result.content.find((entry) => entry.type === "text").text);
}

assert.equal(homedir(), "/root", "The container's default home must remain unchanged");
assert.equal(process.env.HOME, "/root");
assert.equal(process.env.CODEX_HOME, undefined);
assert.equal(existsSync("/root/.codex"), false, "Installed-client qualification requires a fresh container home");
assert.deepEqual(readdirSync("/sys/class/net").sort(), ["lo"], "The test container must have no external network interface");
mkdirSync(workspace, { mode: 0o700 });
const version = command(["--version"]);
assert.equal(version, "codex-cli 0.144.3");
command(["plugin", "marketplace", "add", marketplace, "--json"]);
command(["plugin", "add", pluginId, "--json"]);
const cacheBase = `/root/.codex/plugins/cache/community-plugins/${pluginName}`;
const versions = readdirSync(cacheBase);
assert.equal(versions.length, 1);
const installedRoot = join(cacheBase, versions[0]);
const expectedPackageHashes = treeHashes(join(marketplace, "plugins", pluginName));
assert.deepEqual(treeHashes(installedRoot), expectedPackageHashes, "The actual installer must preserve shipped package bytes");
const configPath = "/root/.codex/config.toml";
const installedConfig = readFileSync(configPath, "utf8");

function configureLauncher(environment) {
  assert.deepEqual(Object.keys(environment).sort(), Object.keys(expectedEnvironment).sort(), "Only the five reviewed runtime settings may configure the installed MCP");
  if (launcher === "plugin") return;
  // This is the real client's supported user-owned TOML configuration, inside
  // the container's fresh home. Only the bundled MCP is disabled; plugin skills
  // remain enabled. No inherited shell runtime settings are available here.
  for (const name of Object.keys(expectedEnvironment)) assert.equal(process.env[name], undefined);
  writeFileSync(configPath, [
    installedConfig, "",
    `[plugins.${JSON.stringify(pluginId)}.mcp_servers.${JSON.stringify(pluginName)}]`,
    "enabled = false",
    "",
    `[mcp_servers.${standaloneName}]`,
    'command = "node"',
    'args = ["./mcp/server.mjs", "--stdio"]',
    `cwd = ${JSON.stringify(installedRoot)}`,
    "tool_timeout_sec = 3600",
    "",
    `[mcp_servers.${standaloneName}.env]`,
    ...Object.entries(environment).map(([name, value]) => `${name} = ${JSON.stringify(value)}`),
    "",
  ].join("\n"), { mode: 0o600 });
}

async function startInstalledClient(environment) {
  configureLauncher(environment);
  const server = new AppServer(environment);
  try {
    await server.request("initialize", {
      clientInfo: { name: "community-grafana-installed-e2e", version: "1.0.0" },
      capabilities: { experimentalApi: true },
    });
    server.notify("initialized");
    const started = await server.request("thread/start", {
      cwd: workspace, ephemeral: true, approvalPolicy: "never", sandbox: "read-only", experimentalRawEvents: false,
    });
    const threadId = started.thread.id;
    assert.equal(typeof threadId, "string");
    const serverName = launcher === "plugin" ? pluginName : standaloneName;
    // Status discovery opens a separate probe client in Codex 0.144.3. Wait for
    // the persistent task client before probing, avoiding first-use state races.
    const startup = await server.waitForMcpStartup(threadId, serverName);
    assert.equal(startup.status, "ready");
    const finalListing = await server.request("mcpServerStatus/list", { threadId, detail: "toolsAndAuthOnly" });
    const status = finalListing.data.find((entry) => entry.serverInfo?.name === pluginName);
    assert.ok(status, "Installed Grafana MCP did not start");
    assert.equal(status.name, serverName);
    assert.deepEqual(finalListing.data.filter((entry) => entry.serverInfo !== null).map((entry) => entry.name), [status.name], "Only the selected MCP launcher may start");
    if (launcher === "standalone-config") {
      // The disabled declaration remains discoverable but launches no process.
      const disabled = finalListing.data.find((entry) => entry.name === pluginName);
      assert.ok(disabled, "The bundled MCP declaration should remain installed");
      assert.equal(disabled.serverInfo, null);
      assert.deepEqual(disabled.tools, {});
    }
    assert.equal(status.serverInfo.name, pluginName);
    assert.equal(status.serverInfo.version, "0.1.0");
    assert.deepEqual(Object.values(status.tools).map((tool) => tool.name).sort(), expectedTools);
    for (const tool of Object.values(status.tools)) {
      assert.equal(tool.annotations.destructiveHint, false);
      assert.equal(tool.inputSchema.additionalProperties, false);
    }
    const skillListing = await server.request("skills/list", { cwds: [workspace], forceReload: true });
    const entry = skillListing.data.find((item) => item.cwd === workspace);
    assert.ok(entry, "The client did not return workspace skill discovery");
    assert.deepEqual(entry.errors, []);
    const skills = entry.skills.filter((skill) => skill.path.startsWith(`${installedRoot}/skills/`));
    assert.deepEqual(skills.map((skill) => skill.name).sort(), expectedSkills.map((name) => `${pluginName}:${name}`));
    for (const skill of skills) {
      assert.equal(skill.enabled, true);
      assert.equal(realpathSync(skill.path), join(installedRoot, "skills", skill.name.slice(pluginName.length + 1), "SKILL.md"));
    }
    const processProof = inspectOwnedMcpProcess(installedRoot, environment);
    return {
      server, startup, serverName, processProof,
      skills: skills.map((skill) => skill.name).sort(),
      // The adapter has no separate stderr channel. App-server diagnostics are
      // kept by `server`, while protocol/startup/tool failures reject directly.
      stderr: "",
      callTool: (tool, args) => server.request("mcpServer/tool/call", { threadId, server: status.name, tool, arguments: args }),
      close: () => server.close(),
    };
  } catch (error) {
    await server.close();
    throw new Error(`${error.message}\nInstalled-client diagnostics: ${JSON.stringify(server.diagnosticSummary())}`);
  }
}

let fixture;
const defaultClient = await startInstalledClient(expectedEnvironment);
try {
  const profile = toolData(await defaultClient.callTool("grafana_profile_status", { profileAlias: "fixture" }));
  assert.equal(profile.status, "COMPLETE");
  assert.equal(profile.readiness, "READY");
  assert.deepEqual(profile.originModes, ["SIMULATED", "REPLAY"]);
  assert.ok(profile.checks.every((check) => check.status === "PASS"));
  const packs = toolData(await defaultClient.callTool("grafana_list_packs", { profileAlias: "fixture" }));
  assert.equal(packs.status, "EMPTY");
  assert.deepEqual(packs.packs, [], "Default fixture startup must not imply live Grafana authority");
  const rejected = await defaultClient.callTool("grafana_run_pack", {
    idempotencyKey: "installed-inactive-denial", profileAlias: "fixture", packId: "grafana.apm",
    scopeAlias: "checkout", origin: "SIMULATED", window: syntheticWindow(),
  });
  assert.equal(rejected.isError, true);
  assert.equal(rejected.structuredContent?.status, "ERROR");
  assert.equal(rejected.structuredContent?.error?.code, "PACK_NOT_ACTIVE");
  fixture = { profile: profile.status, packs: packs.status, rejection: rejected.structuredContent.error.code };
} catch (error) {
  throw new Error(`${error.message}\nInstalled-client diagnostics: ${JSON.stringify(defaultClient.server.diagnosticSummary())}`);
} finally {
  assert.deepEqual(await defaultClient.close(), { code: 0, signal: null });
}

// The first client is closed before another starts: both otherwise contend for
// Codex's shared local state. All authority and provider state below is disposable.
let syntheticClient;
const session = await startSyntheticSession({
  pluginRoot: installedRoot,
  startClient: async ({ pluginRoot, env }) => {
    assert.equal(pluginRoot, realpathSync(installedRoot));
    assert.deepEqual(treeHashes(pluginRoot), expectedPackageHashes);
    const environment = Object.fromEntries(Object.keys(expectedEnvironment).map((name) => {
      assert.equal(typeof env[name], "string", `Missing synthetic setting ${name}`);
      return [name, env[name]];
    }));
    syntheticClient = await startInstalledClient(environment);
    return syntheticClient;
  },
});
let synthetic;
let cleanup;
try {
  const result = await collectSyntheticIncident(session);
  assert.equal(result.profile.status, "PARTIAL");
  assert.equal(result.profile.readiness, "WARN");
  assert.equal(result.report.status, "COMPLETE");
  assert.equal(result.report.origin, "SIMULATED");
  assert.equal(result.transport.providerReads, 10);
  assert.equal(result.transport.unexpectedRequests, 0);
  assert.equal(result.evidence.length, 10);
  assert.equal(result.links.length, 10);
  assert.ok(result.evidence.every((detail) => detail.reportRef === result.report.reportRef && detail.origin === "SIMULATED"));
  assert.ok(result.links.every((link) => link.reportRef === result.report.reportRef));
  assert.ok(result.report.issues.some((issue) => issue.kind === "REGRESSION" && issue.classification === "SIGNED_RULE"));
  assert.deepEqual(treeHashes(installedRoot), expectedPackageHashes, "Collection must not rewrite the installed candidate");
  synthetic = {
    origin: result.report.origin, profileStatus: result.profile.status, readiness: result.profile.readiness,
    reportStatus: result.report.status, providerReads: result.transport.providerReads,
    evidenceCount: result.evidence.length, sourceLinkCount: result.links.length, ownedReferences: true,
    unexpectedRequests: result.transport.unexpectedRequests, processProof: syntheticClient.processProof,
  };
} catch (error) {
  throw new Error(`${error.message}\nInstalled-client diagnostics: ${JSON.stringify(syntheticClient.server.diagnosticSummary())}`);
} finally {
  cleanup = await session.close();
}
console.log(JSON.stringify({
  codexVersion: version, launcher, installedRoot, serverName: defaultClient.serverName, tools: expectedTools,
  skills: defaultClient.skills, taskScopedStartup: defaultClient.startup.status,
  fixtureProfile: fixture.profile, fixturePacks: fixture.packs, inactiveCollection: fixture.rejection,
  network: "none", modelTurnsSubmitted: 0, hostConfigurationMounted: false,
  installedBytesPreserved: true, processProof: defaultClient.processProof,
  synthetic: { ...synthetic, cleanup }, realGrafana: false, diagnosis: "NOT_ESTABLISHED", recovery: "NOT_ASSESSED",
}));

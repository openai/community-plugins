import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { chmodSync, cpSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const REPOSITORY_ROOT = fileURLToPath(new URL("../../../", import.meta.url));
export const SOURCE_PLUGIN_ROOT = join(REPOSITORY_ROOT, "plugins/grafana-observability");
const ownedSandboxes = new Set();

// Never inherit the developer's monitoring credentials, NODE_OPTIONS, or active
// trust root. Tests opt in to each additional environment value explicitly.
export function isolatedEnvironment(overrides = {}) {
  const environment = {};
  for (const key of ["PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP"]) {
    if (process.env[key] !== undefined) environment[key] = process.env[key];
  }
  return { ...environment, ...overrides };
}

export function createSandbox(t, { copyPlugin = true, sourcePluginRoot = SOURCE_PLUGIN_ROOT } = {}) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "community-grafana-e2e-")));
  ownedSandboxes.add(root);
  try {
    const pluginRoot = copyPlugin ? join(root, "plugin") : sourcePluginRoot;
    if (copyPlugin) cpSync(sourcePluginRoot, pluginRoot, { recursive: true, dereference: false });
    const artifactRoot = join(root, "artifacts");
    const stateRoot = join(root, "state");
    const dataRoot = join(root, "data");
    mkdirSync(dataRoot, { mode: 0o700 });
    t?.after(() => removeSandbox(root));
    return {
      root,
      pluginRoot,
      artifactRoot,
      stateRoot,
      env: isolatedEnvironment({
        XDG_DATA_HOME: dataRoot,
        PRODUCTION_MONITORING_MODE: "fixture",
        PRODUCTION_MONITORING_ARTIFACT_ROOT: artifactRoot,
        PRODUCTION_MONITORING_STATE_ROOT: stateRoot,
        PRODUCTION_MONITORING_PRINCIPAL_SESSION: "packaged-grafana-e2e",
      }),
    };
  } catch (error) {
    // Construction can fail before a caller receives the owned root to clean.
    try { removeSandbox(root); }
    catch (cleanupError) { throw new AggregateError([error, cleanupError], "Sandbox setup and cleanup failed"); }
    throw error;
  }
}

// Sealed evidence intentionally uses read-only directories. Restore owner write
// permission only inside a temp root issued by this helper, never via symlinks.
export function removeSandbox(root) {
  assert.ok(ownedSandboxes.has(root), "Refusing to remove a sandbox this process did not create");
  function unlock(directory) {
    let stat;
    try { stat = lstatSync(directory); } catch (error) {
      if (error.code === "ENOENT") return;
      throw error;
    }
    assert.ok(stat.isDirectory() && !stat.isSymbolicLink(), "Sandbox directory was replaced");
    if (typeof process.getuid === "function") assert.equal(stat.uid, process.getuid());
    chmodSync(directory, 0o700);
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory() && !entry.isSymbolicLink()) unlock(join(directory, entry.name));
    }
  }
  unlock(root);
  rmSync(root, { recursive: true, force: true, maxRetries: 3 });
  ownedSandboxes.delete(root);
}

function within(root, value) {
  const result = resolve(root, value);
  const rel = relative(root, result);
  assert.ok(rel !== ".." && !rel.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) && !isAbsolute(rel));
  return result;
}

export function packageLaunch(pluginRoot) {
  const manifest = JSON.parse(readFileSync(join(pluginRoot, ".codex-plugin/plugin.json"), "utf8"));
  const config = JSON.parse(readFileSync(within(pluginRoot, manifest.mcpServers), "utf8"));
  const launch = config.mcpServers[manifest.name];
  assert.equal(launch.command, "node");
  assert.deepEqual(launch.args, ["./mcp/server.mjs", "--stdio"]);
  const cwd = within(pluginRoot, launch.cwd ?? ".");
  assert.ok(lstatSync(within(cwd, launch.args[0])).isFile());
  return { command: process.execPath, args: launch.args, cwd };
}

function delay(milliseconds) {
  let timer;
  const promise = new Promise((resolvePromise) => { timer = setTimeout(resolvePromise, milliseconds); });
  return { promise, cancel: () => clearTimeout(timer) };
}

/** A small real JSON-RPC/stdio client: no MCP SDK or ancestor node_modules. */
export class McpClient {
  #child;
  #pending = new Map();
  #nextId = 1;
  #buffer = "";
  #closed;
  #exit;
  #protocolError;
  stderr = "";
  notifications = [];
  initialization;

  constructor(pluginRoot, { env = isolatedEnvironment(), timeoutMs = 30_000 } = {}) {
    this.timeoutMs = timeoutMs;
    const launch = packageLaunch(pluginRoot);
    this.#child = spawn(launch.command, launch.args, {
      cwd: launch.cwd,
      env,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.#child.stdout.setEncoding("utf8");
    this.#child.stderr.setEncoding("utf8");
    this.#child.stdout.on("data", (chunk) => this.#receive(chunk));
    this.#child.stderr.on("data", (chunk) => { this.stderr += chunk; });
    this.#child.on("error", (error) => this.#fail(error));
    this.#child.stdin.on("error", (error) => this.#fail(error));
    this.#closed = new Promise((resolveClosed) => {
      this.#child.once("close", (code, signal) => {
        this.#exit = { code, signal };
        this.#fail(new Error(`MCP server closed (${code ?? signal ?? "unknown"})`));
        resolveClosed(this.#exit);
      });
    });
  }

  static async start(pluginRoot, options) {
    const client = new McpClient(pluginRoot, options);
    try {
      client.initialization = await client.request("initialize", {
        protocolVersion: "2025-03-26",
        capabilities: {},
        clientInfo: { name: "community-grafana-packaged-e2e", version: "1.0.0" },
      });
      client.notify("notifications/initialized");
      return client;
    } catch (error) {
      await client.close();
      throw error;
    }
  }

  #fail(error) {
    for (const { reject, timer } of this.#pending.values()) {
      clearTimeout(timer);
      reject(error);
    }
    this.#pending.clear();
  }

  #receive(chunk) {
    this.#buffer += chunk;
    if (this.#buffer.length > 8 * 1024 * 1024) {
      this.#protocolError = new Error("MCP stdout exceeded the test client's message bound");
      this.#fail(this.#protocolError);
      this.#child.kill();
      return;
    }
    let newline;
    while ((newline = this.#buffer.indexOf("\n")) !== -1) {
      const line = this.#buffer.slice(0, newline).replace(/\r$/u, "");
      this.#buffer = this.#buffer.slice(newline + 1);
      if (line.length === 0) continue;
      let message;
      try {
        message = JSON.parse(line);
        assert.equal(message.jsonrpc, "2.0");
      } catch (cause) {
        this.#protocolError = new Error("Non-JSON-RPC output on MCP stdout", { cause });
        this.#fail(this.#protocolError);
        this.#child.kill();
        return;
      }
      if (Object.hasOwn(message, "id") && this.#pending.has(message.id)) {
        const pending = this.#pending.get(message.id);
        this.#pending.delete(message.id);
        clearTimeout(pending.timer);
        if (message.error) {
          const error = new Error(message.error.message);
          error.code = message.error.code;
          error.data = message.error.data;
          pending.reject(error);
        } else {
          pending.resolve(message.result);
        }
      } else {
        this.notifications.push(message);
      }
    }
  }

  request(method, params = {}, { timeoutMs = this.timeoutMs } = {}) {
    if (this.#exit) return Promise.reject(new Error("MCP server is already closed"));
    if (this.#protocolError) return Promise.reject(this.#protocolError);
    const id = this.#nextId++;
    return new Promise((resolveRequest, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        reject(new Error(`MCP request timed out: ${method}`));
      }, timeoutMs);
      this.#pending.set(id, { resolve: resolveRequest, reject, timer });
      this.#child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
    });
  }

  notify(method, params) {
    this.#child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method, ...(params === undefined ? {} : { params }) })}\n`);
  }

  callTool(name, args = {}, options) {
    return this.request("tools/call", { name, arguments: args }, options);
  }

  async close() {
    if (!this.#exit) this.#child.stdin.end();
    const graceful = delay(5_000);
    try {
      await Promise.race([this.#closed, graceful.promise]);
    } finally {
      graceful.cancel();
    }
    if (!this.#exit) {
      this.#child.kill("SIGTERM");
      const termination = delay(5_000);
      try {
        await Promise.race([this.#closed, termination.promise]);
      } finally {
        termination.cancel();
      }
    }
    if (!this.#exit) this.#child.kill("SIGKILL");
    return this.#closed;
  }
}

export function toolData(result) {
  assert.notEqual(result.isError, true, JSON.stringify(result.structuredContent ?? result.content));
  assert.ok(result.structuredContent && typeof result.structuredContent === "object");
  assert.deepEqual(JSON.parse(result.content.find((item) => item.type === "text").text), result.structuredContent);
  return result.structuredContent;
}

export function assertNoAncestorDependencies(pluginRoot) {
  for (let current = resolve(pluginRoot); ; current = dirname(current)) {
    let exists = false;
    try { exists = lstatSync(join(current, "node_modules")).isDirectory(); } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    assert.equal(exists, false, `Unexpected ancestor node_modules: ${current}`);
    if (dirname(current) === current) break;
  }
}

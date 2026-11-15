import { once } from "node:events";
import { spawn } from "node:child_process";
class McpProcess {
  child;
  buffer = "";
  pending = /* @__PURE__ */ new Map();
  failure;
  constructor(options = {}) {
    const env = { ...process.env };
    for (const name of Object.keys(env)) {
      if (name.startsWith("ELECTRON_DEBUG_")) delete env[name];
    }
    this.child = spawn(process.execPath, [...options.nodeArgs ?? [], options.entry ?? "dist/index.js"], {
      stdio: ["pipe", "pipe", "pipe"],
      cwd: options.cwd,
      env: { ...env, ...options.env }
    });
    this.child.once("error", (error) => this.fail(error));
    this.child.stdin.on("error", (error) => this.fail(error));
    this.child.once("exit", (code, signal) => this.fail(new Error(`MCP process exited: ${code ?? signal}`)));
    this.child.stdout.on("data", (chunk) => {
      this.buffer += chunk.toString();
      const lines = this.buffer.split("\n");
      this.buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) {
          continue;
        }
        let message;
        try {
          message = JSON.parse(line);
        } catch {
          this.fail(new Error("MCP stdout contained invalid JSON"));
          continue;
        }
        if (typeof message.id === "number") {
          const pending = this.pending.get(message.id);
          if (pending) {
            clearTimeout(pending.timer);
            pending.resolve(message);
          }
          this.pending.delete(message.id);
        }
      }
    });
  }
  async request(id, method, params) {
    if (this.failure) throw this.failure;
    const response = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`MCP request timed out: ${method}`));
      }, 5e3);
      this.pending.set(id, { resolve, reject, timer });
    });
    this.child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}
`);
    return response;
  }
  notify(method, params) {
    this.child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method, params })}
`);
  }
  fail(error) {
    this.failure = error;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
  async waitForExit(timeoutMs = 3e3) {
    if (this.child.exitCode !== null || this.child.signalCode !== null) return;
    await once(this.child, "exit", { signal: AbortSignal.timeout(timeoutMs) });
  }
  async close() {
    if (this.child.exitCode !== null || this.child.signalCode !== null) {
      return;
    }
    const exited = once(this.child, "exit");
    this.child.kill();
    await exited.catch(() => void 0);
  }
}
export {
  McpProcess
};

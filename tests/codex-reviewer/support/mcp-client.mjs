import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { createInterface } from "node:readline";

// Built-in modules only: exercise the committed package before npm ci.
export async function connect(pluginRoot) {
  const config = JSON.parse(await readFile(path.join(pluginRoot, "mcp.json"), "utf8"));
  const server = config.mcpServers["git-reviewer"];
  if (server.command !== "node") throw new Error("Expected the Node stdio entrypoint.");
  const child = spawn(process.execPath, server.args.map((arg) => arg.replaceAll("${PLUGIN_ROOT}", pluginRoot)), {
    cwd: pluginRoot, windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
  });
  const pending = new Map();
  let sequence = 0;
  let stderr = "";
  child.stderr.on("data", (bytes) => { stderr = (stderr + bytes).slice(-4096); });
  const rejectPending = (error) => {
    for (const entry of pending.values()) { clearTimeout(entry.timer); entry.reject(error); }
    pending.clear();
  };
  child.on("error", rejectPending);
  child.on("exit", (code) => rejectPending(new Error(`MCP exited ${code}: ${stderr}`)));
  createInterface({ input: child.stdout }).on("line", (line) => {
    let message;
    try { message = JSON.parse(line); } catch (error) { rejectPending(error); return; }
    const entry = pending.get(message.id);
    if (!entry) return;
    pending.delete(message.id);
    clearTimeout(entry.timer);
    if (message.error) entry.reject(new Error(message.error.message));
    else entry.resolve(message.result);
  });
  function request(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`MCP timeout: ${method}`)); }, 15000);
      pending.set(id, { resolve, reject, timer });
      child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
    });
  }
  async function close() {
    rejectPending(new Error("MCP client closed"));
    if (child.exitCode !== null || child.signalCode !== null) return;
    await new Promise((resolve) => {
      const timer = setTimeout(() => child.kill(), 2000);
      child.once("exit", () => { clearTimeout(timer); resolve(); });
      child.stdin.end();
    });
  }
  try {
    const initialized = await request("initialize", {
      protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "reviewer-cold-test", version: "1.0.0" },
    });
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" })}\n`);
    return { initialized, request, close, call: (name, args) => request("tools/call", { name, arguments: args }) };
  } catch (error) { await close(); throw error; }
}

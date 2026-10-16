import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { createInterface } from "node:readline";
import test from "node:test";
import { createFixtureRepository } from "../fixtures/git-repo.mjs";
import { readThreadProjectRoot } from "../../src/server/context.ts";

test("Codex discovers this plugin's embedded review entrypoints and UI resource", {
  skip: !process.env.CODEX_HOST_CLI && "Set CODEX_HOST_CLI to the installed desktop CLI executable.",
  timeout: 90_000,
}, async (t) => {
  const rootPath = process.cwd();
  const manifest = JSON.parse(await readFile(path.join(rootPath, "plugin.json"), "utf8"));
  const marketplace = JSON.parse(await readFile(path.resolve(rootPath, "../../.agents/plugins/marketplace.json"), "utf8"));
  const pluginId = `${manifest.name}@${marketplace.name}`;
  const child = spawn(process.env.CODEX_HOST_CLI, ["app-server", "--stdio"], {
    cwd: rootPath,
    windowsHide: true,
    stdio: ["pipe", "pipe", "ignore"],
  });
  const pending = new Map();
  let sequence = 0;

  createInterface({ input: child.stdout }).on("line", (line) => {
    const message = JSON.parse(line);
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id);
    clearTimeout(request.timer);
    if (message.error) request.reject(new Error(message.error.message));
    else request.resolve(message.result);
  });

  child.on("error", (error) => {
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(error);
    }
    pending.clear();
  });

  t.after(async () => {
    for (const request of pending.values()) clearTimeout(request.timer);
    child.stdin.end();
    await new Promise((resolve) => {
      const timer = setTimeout(() => { child.kill(); resolve(); }, 2_000);
      child.once("exit", () => { clearTimeout(timer); resolve(); });
    });
  });

  function request(method, params) {
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`Codex request timed out: ${method}`));
      }, 30_000);
      pending.set(id, { resolve, reject, timer });
      child.stdin.write(`${JSON.stringify({ id, method, params })}\n`);
    });
  }

  await request("initialize", {
    clientInfo: { name: "codex-reviewer-host-check", version: manifest.version },
    capabilities: { experimentalApi: true },
  });
  child.stdin.write(`${JSON.stringify({ method: "initialized" })}\n`);

  const inventory = await request("mcpServerStatus/list", { limit: 100, detail: "full" });
  const server = inventory.data.find((entry) => entry.pluginId === pluginId);
  assert.ok(server, "Codex must load the review server under this plugin, without a built-in server replacing it.");
  assert.equal(server.toolsError, null);
  const entrypoint = Object.values(server.tools).find((tool) => tool.name === "code-review.open");
  assert.ok(entrypoint, "Codex must preserve the app launcher in its discovered tool catalog.");
  assert.equal(entrypoint.title, "Reviewer");
  assert.ok(entrypoint._meta.ui.visibility.includes("model"), "Opening the panel must also be available from a user request in chat.");
  assert.deepEqual(entrypoint._meta["openai/ui"].entrypoints, [{ type: "thread" }]);

  // 临时运行时不写入会话历史，用于验证与桌面面板相同的带上下文调用路径。
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  const runtime = await request("thread/start", { cwd: fixture.rootPath, ephemeral: true });
  const opened = await request("mcpServer/tool/call", {
    server: server.name,
    threadId: runtime.thread.id,
    tool: entrypoint.name,
    arguments: { rootPath: fixture.rootPath },
  });
  assert.notEqual(opened.isError, true);
  assert.equal(opened.structuredContent.ready, true);
  assert.equal(opened.structuredContent.rootPath, fixture.rootPath);
  const review = await request("mcpServer/tool/call", {
    server: server.name, threadId: runtime.thread.id, tool: "code-review.getReview",
    arguments: { rootPath: fixture.rootPath, scope: { kind: "worktree" } },
  });
  const source = await request("mcpServer/tool/call", {
    server: server.name, threadId: runtime.thread.id, tool: "code-review.getFile",
    arguments: { snapshotId: review.structuredContent.snapshotId, path: "README.md" },
  });
  assert.notEqual(source.isError, true);
  assert.equal(source.structuredContent.mode, "file");
  assert.equal(source.structuredContent.text, "base\n");

  // 已保存的当前项目会话可验证无参数侧栏入口；临时会话不在其他后端持久化。
  if (process.env.CODEX_CONTEXT_THREAD_ID) {
    const automatic = await readThreadProjectRoot(process.env.CODEX_CONTEXT_THREAD_ID, {
      executable: process.env.CODEX_HOST_CLI, args: [],
    });
    assert.equal(automatic, rootPath);
  }

  const resource = await request("mcpServer/resource/read", {
    server: server.name,
    threadId: runtime.thread.id,
    uri: entrypoint._meta.ui.resourceUri,
  });
  assert.equal(resource.contents[0].mimeType, "text/html;profile=mcp-app");
  assert.deepEqual(resource.contents[0]._meta["openai/ui"], {
    preferredDisplayMode: "fullscreen", availableDisplayModes: ["fullscreen"],
  });
  const builtApp = await readFile(path.join(rootPath, "dist", "app", "index.html"), "utf8");
  assert.equal(createHash("sha256").update(resource.contents[0].text).digest("hex"), createHash("sha256").update(builtApp).digest("hex"), "Codex must serve the current built app, not a previous version");
  assert.match(resource.contents[0].text, /review-app/);
  assert.doesNotMatch(resource.contents[0].text, /Repository folder path/);
  assert.doesNotMatch(resource.contents[0].text, /data:font\/woff;base64,|CodexSeti/);
  assert.match(resource.contents[0].text, /viewBox/);
  assert.match(resource.contents[0].text, /data-file-icon/);
});

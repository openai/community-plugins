import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFile, mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { createFixtureRepository } from "../fixtures/git-repo.mjs";

async function exerciseDistribution(t, pluginRoot) {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  await fixture.write("README.md", "installed distribution change\n");
  const config = JSON.parse(await readFile(path.join(pluginRoot, "mcp.json"), "utf8"));
  const server = config.mcpServers["git-reviewer"];
  assert.equal(server.command, "node");
  const client = new Client({ name: "reviewer-distribution-check", version: "1.0.0" }, { capabilities: {} });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: server.args.map((arg) => arg.replaceAll("${PLUGIN_ROOT}", pluginRoot)),
    cwd: pluginRoot,
  });
  try {
    await client.connect(transport);
    const manifest = JSON.parse(await readFile(path.resolve("plugin.json"), "utf8"));
    assert.equal(client.getServerVersion().version, manifest.version);
    const tools = await client.listTools();
    assert.ok(tools.tools.some((tool) => tool.name === "code-review.open"));
    const app = await client.readResource({ uri: "ui://code-review/review.html" });
    assert.deepEqual(app.contents[0]._meta["openai/ui"].availableDisplayModes, ["fullscreen"]);
    assert.match(app.contents[0].text, /review-app/);
    const expectedApp = await readFile(path.resolve("dist/app/index.html"), "utf8");
    const digest = (text) => createHash("sha256").update(text).digest("hex");
    assert.equal(digest(app.contents[0].text), digest(expectedApp), "installed UI matches the release build byte for byte");
    const captured = await client.callTool({
      name: "code-review.getReview",
      arguments: { rootPath: fixture.rootPath, scope: { kind: "worktree" } },
    });
    assert.notEqual(captured.isError, true);
    const file = await client.callTool({
      name: "code-review.getFile", arguments: { snapshotId: captured.structuredContent.snapshotId, path: "README.md" },
    });
    assert.equal(file.structuredContent.fileDiff.oldText, "base\n");
    assert.equal(file.structuredContent.fileDiff.newText, "installed distribution change\n");
    const notices = await readFile(path.join(pluginRoot, "dist", "THIRD_PARTY_NOTICES.txt"), "utf8");
    for (const name of ["monaco-editor", "react", "@modelcontextprotocol/server", "@openai/mcp-extensions", "VS Code Seti"]) {
      assert.ok(notices.includes(name), `distribution retains ${name} licensing`);
    }
  } finally {
    await client.close();
  }
}

test("built distribution starts without source files or node_modules", async (t) => {
  const isolated = await mkdtemp(path.join(tmpdir(), "reviewer-distribution-"));
  t.after(() => rm(isolated, { recursive: true, force: true }));
  for (const file of ["package.json", "plugin.json", "mcp.json", "dist/server/index.js", "dist/app/index.html"]) {
    const destination = path.join(isolated, file);
    await mkdir(path.dirname(destination), { recursive: true });
    await copyFile(path.resolve(file), destination);
  }
  // 先验证运行时自包含，再检查发布所需的许可文件。
  try { await copyFile(path.resolve("dist/THIRD_PARTY_NOTICES.txt"), path.join(isolated, "dist/THIRD_PARTY_NOTICES.txt")); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
  await exerciseDistribution(t, isolated);
});

test("GitHub marketplace installs and starts in an isolated Codex home", {
  skip: !(process.env.CODEX_HOST_CLI && process.env.CODEX_MARKETPLACE_REPOSITORY) && "Set CODEX_HOST_CLI and CODEX_MARKETPLACE_REPOSITORY to test a published Git marketplace.",
  timeout: 120_000,
}, async (t) => {
  const isolated = await mkdtemp(path.join(tmpdir(), "reviewer-marketplace-"));
  t.after(() => rm(isolated, { recursive: true, force: true }));
  const env = { ...process.env, CODEX_HOME: isolated };
  const run = (args) => JSON.parse(execFileSync(process.env.CODEX_HOST_CLI, args, {
    env, encoding: "utf8", timeout: 60_000, windowsHide: true,
  }));
  run(["plugin", "marketplace", "add", process.env.CODEX_MARKETPLACE_REPOSITORY, ...(process.env.CODEX_MARKETPLACE_REF ? ["--ref", process.env.CODEX_MARKETPLACE_REF] : []), "--json"]);
  const installed = run(["plugin", "add", "codex-reviewer@community-plugins", "--json"]);
  const manifest = JSON.parse(await readFile(path.resolve("plugin.json"), "utf8"));
  assert.equal(installed.version, manifest.version);
  assert.ok(path.relative(isolated, installed.installedPath).split(path.sep)[0] !== "..");
  await exerciseDistribution(t, installed.installedPath);
});

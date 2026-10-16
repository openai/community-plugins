import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { createFixtureRepository } from "../fixtures/git-repo.mjs";

test("local Codex marketplace starts the review app and serves Git diff tools", async (t) => {
  const rootPath = process.cwd();
  const marketplacePath = path.resolve(rootPath, "../../.agents/plugins/marketplace.json");
  const marketplace = JSON.parse(await readFile(marketplacePath, "utf8"));
  const pluginEntry = marketplace.plugins.find((plugin) => plugin.name === "codex-reviewer");

  assert.ok(pluginEntry, "local marketplace must expose codex-reviewer");
  assert.equal(pluginEntry.source.source, "local");
  assert.ok(pluginEntry.policy.installation);
  assert.ok(pluginEntry.policy.authentication);
  assert.ok(pluginEntry.category);

  const pluginRoot = path.resolve(rootPath, "../..", pluginEntry.source.path);
  const manifest = JSON.parse(await readFile(path.join(pluginRoot, "plugin.json"), "utf8"));
  assert.equal(manifest.name, "codex-reviewer");

  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  fixture.git("switch", "-c", "feature");
  await fixture.write("README.md", "feature version\n");
  fixture.commit("feature change");

  const client = new Client({ name: "code-review-e2e", version: "1.0.0" }, { capabilities: {} });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.join(pluginRoot, "dist", "server", "index.js")],
    cwd: pluginRoot,
  });
  t.after(async () => client.close());
  await client.connect(transport);

  const tools = await client.listTools();
  const openTool = tools.tools.find((tool) => tool.name === "code-review.open");
  assert.ok(openTool);
  assert.deepEqual(openTool._meta["openai/ui"].entrypoints, [{ type: "thread" }]);

  const appResource = await client.readResource({ uri: "ui://code-review/review.html" });
  assert.deepEqual(appResource.contents[0]._meta["openai/ui"], {
    preferredDisplayMode: "fullscreen", availableDisplayModes: ["fullscreen"],
  });
  assert.match(appResource.contents[0].text, /Reviewer/);

  const repositoryResult = await client.callTool({
    name: "code-review.listRepositories",
    arguments: { rootPath: fixture.rootPath },
  });
  assert.equal(repositoryResult.structuredContent.rootPath, fixture.rootPath);

  const reviewResult = await client.callTool({
    name: "code-review.getReview",
    arguments: {
      rootPath: fixture.rootPath,
      scope: { kind: "branch", baseRef: "refs/heads/main" },
    },
  });
  const review = reviewResult.structuredContent;
  const changedFile = review.changedFiles.find((file) => file.newPath === "README.md");
  assert.ok(changedFile);

  const fileResult = await client.callTool({
    name: "code-review.getFileDiff",
    arguments: { snapshotId: review.snapshotId, fileId: changedFile.id },
  });
  assert.equal(fileResult.structuredContent.oldText, "base\n");
  assert.equal(fileResult.structuredContent.newText, "feature version\n");

  const freshness = await client.callTool({
    name: "code-review.isReviewStale",
    arguments: { snapshotId: review.snapshotId },
  });
  assert.equal(freshness.structuredContent.stale, false);

  const unborn = await createFixtureRepository({ initialCommit: false });
  t.after(unborn.cleanup);
  const firstReview = await client.callTool({
    name: "code-review.getReview", arguments: { rootPath: unborn.rootPath, scope: { kind: "worktree" } },
  });
  assert.notEqual(firstReview.isError, true);
  assert.equal(firstReview.structuredContent.headRevision, null);
  assert.equal(firstReview.structuredContent.changedFiles[0].status, "added");
  const source = await client.callTool({
    name: "code-review.getFile", arguments: { snapshotId: firstReview.structuredContent.snapshotId, path: "README.md" },
  });
  assert.notEqual(source.isError, true);
  assert.equal(source.structuredContent.mode, "file");
  assert.equal(source.structuredContent.text, "base\n");
});

test("installed plugin cache can start its MCP server and serve the bundled app", async (t) => {
  const rootPath = process.cwd();
  const marketplace = JSON.parse(await readFile(path.resolve(rootPath, "../../.agents/plugins/marketplace.json"), "utf8"));
  const manifest = JSON.parse(await readFile(path.join(rootPath, "plugin.json"), "utf8"));
  const codexHome = process.env.CODEX_HOME ?? path.join(homedir(), ".codex");
  const installedRoot = path.join(codexHome, "plugins", "cache", marketplace.name, manifest.name, manifest.version);
  if (!existsSync(installedRoot)) {
    t.skip("Install the local plugin to exercise its Codex cache copy.");
    return;
  }

  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  const client = new Client({ name: "code-review-installed-e2e", version: "1.0.0" }, { capabilities: {} });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.join(installedRoot, "dist", "server", "index.js")],
    cwd: installedRoot,
  });
  t.after(async () => client.close());
  await client.connect(transport);

  const tools = await client.listTools();
  assert.ok(tools.tools.some((tool) => tool.name === "code-review.getReview"));
  const resource = await client.readResource({ uri: "ui://code-review/review.html" });
  assert.deepEqual(resource.contents[0]._meta["openai/ui"], {
    preferredDisplayMode: "fullscreen", availableDisplayModes: ["fullscreen"],
  });
  assert.ok(resource.contents[0].text.includes("review-app"));
  const repository = await client.callTool({
    name: "code-review.listRepositories",
    arguments: { rootPath: fixture.rootPath },
  });
  assert.equal(repository.structuredContent.rootPath, fixture.rootPath);
  const unborn = await createFixtureRepository({ initialCommit: false });
  t.after(unborn.cleanup);
  const firstReview = await client.callTool({
    name: "code-review.getReview", arguments: { rootPath: unborn.rootPath, scope: { kind: "worktree" } },
  });
  assert.notEqual(firstReview.isError, true);
  assert.equal(firstReview.structuredContent.headRevision, null);
  const source = await client.callTool({
    name: "code-review.getFile", arguments: { snapshotId: firstReview.structuredContent.snapshotId, path: "README.md" },
  });
  assert.notEqual(source.isError, true);
  assert.equal(source.structuredContent.mode, "file");
  assert.equal(source.structuredContent.text, "base\n");
});

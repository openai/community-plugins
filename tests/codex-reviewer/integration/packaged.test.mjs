import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { copyFile, mkdir, mkdtemp, readFile, realpath, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createFixtureRepository } from "../../../plugins/codex-reviewer/tests/fixtures/git-repo.mjs";
import { connect } from "../support/mcp-client.mjs";

const pluginRoot = fileURLToPath(new URL("../../../plugins/codex-reviewer/", import.meta.url));

test("cold runtime serves the released UI and immutable Git diffs without source or dependencies", async (t) => {
  const isolated = await mkdtemp(path.join(tmpdir(), "reviewer-cold-"));
  t.after(() => rm(isolated, { recursive: true, force: true }));
  for (const file of ["package.json", "plugin.json", "mcp.json", "LICENSE", "dist/server/index.js", "dist/app/index.html", "dist/THIRD_PARTY_NOTICES.txt"]) {
    const destination = path.join(isolated, file);
    await mkdir(path.dirname(destination), { recursive: true });
    await copyFile(path.join(pluginRoot, file), destination);
  }
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  await fixture.write("README.md", "first change\n");
  await fixture.write("new.txt", "untracked source\n");
  const client = await connect(isolated);
  try {
    const manifest = JSON.parse(await readFile(path.join(isolated, "plugin.json"), "utf8"));
    assert.equal(client.initialized.serverInfo.version, manifest.version);
    const catalog = await client.request("tools/list");
    const open = catalog.tools.find((tool) => tool.name === "code-review.open");
    assert.deepEqual(open._meta["openai/ui"].entrypoints, [{ type: "thread" }]);
    const opened = await client.call(open.name, { rootPath: fixture.rootPath });
    assert.equal(opened.structuredContent.rootPath, fixture.rootPath);
    const resource = await client.request("resources/read", { uri: open._meta.ui.resourceUri });
    assert.deepEqual(resource.contents[0]._meta["openai/ui"].availableDisplayModes, ["fullscreen"]);
    const hash = (value) => createHash("sha256").update(value).digest("hex");
    assert.equal(hash(resource.contents[0].text), hash(await readFile(path.join(pluginRoot, "dist/app/index.html"))));
    const captured = await client.call("code-review.getReview", { rootPath: fixture.rootPath, scope: { kind: "worktree" } });
    assert.notEqual(captured.isError, true);
    const { snapshotId } = captured.structuredContent;
    await fixture.write("README.md", "later change\n");
    const tracked = await client.call("code-review.getFile", { snapshotId, path: "README.md" });
    assert.equal(tracked.structuredContent.mode, "diff");
    assert.equal(tracked.structuredContent.fileDiff.oldText, "base\n");
    assert.equal(tracked.structuredContent.fileDiff.newText, "first change\n");
    const untracked = await client.call("code-review.getFile", { snapshotId, path: "new.txt" });
    assert.equal(untracked.structuredContent.mode, "file");
    assert.equal(untracked.structuredContent.text, "untracked source\n");
    const freshness = await client.call("code-review.isReviewStale", { snapshotId });
    assert.equal(freshness.structuredContent.stale, true);
  } finally { await client.close(); }
});

test("packaged MCP resolves the selected linked worktree and compares its committed branch", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  const parent = await realpath(await mkdtemp(path.join(tmpdir(), "reviewer-linked-")));
  t.after(() => rm(parent, { recursive: true, force: true }));
  const linked = path.join(parent, "worktree");
  fixture.git("worktree", "add", "-b", "feature-review", linked);
  const { execFileSync } = await import("node:child_process");
  const git = (...args) => execFileSync("git", args, { cwd: linked, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
  const { writeFile } = await import("node:fs/promises");
  await writeFile(path.join(linked, "README.md"), "linked branch\n");
  git("add", "README.md");
  git("commit", "-m", "linked fixture change");
  await fixture.write("main-only.txt", "must not appear in linked tree\n");
  const client = await connect(pluginRoot);
  try {
    const info = await client.call("code-review.listRepositories", { rootPath: linked });
    assert.equal(info.structuredContent.rootPath, linked);
    assert.equal(info.structuredContent.currentBranch, "feature-review");
    const result = await client.call("code-review.getReview", { rootPath: linked, scope: { kind: "branch", baseRef: "refs/heads/main" } });
    assert.notEqual(result.isError, true);
    const snapshot = result.structuredContent;
    assert.ok(!snapshot.tree.some((entry) => entry.path === "main-only.txt"));
    const diff = await client.call("code-review.getFileDiff", { snapshotId: snapshot.snapshotId, fileId: snapshot.changedFiles[0].id });
    assert.equal(diff.structuredContent.oldText, "base\n");
    assert.equal(diff.structuredContent.newText, "linked branch\n");
  } finally { await client.close(); }
});

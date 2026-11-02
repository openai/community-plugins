import assert from "node:assert/strict";
import { readFile, rm, stat, utimes, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { getReview, isReviewStale } from "../src/server/snapshot.ts";
import { createFixtureRepository } from "./fixtures/git-repo.mjs";
import { getFileDiff } from "../src/server/diff.ts";

test("an unborn repository shows staged and untracked files as additions against empty content", async (t) => {
  const fixture = await createFixtureRepository({ initialCommit: false });
  t.after(fixture.cleanup);
  await fixture.write("staged.ts", "export const staged = true;\n");
  fixture.git("add", "staged.ts");
  await fixture.write("staged.ts", "export const staged = false;\n");
  await fixture.write("removed.txt", "not present in the worktree\n");
  fixture.git("add", "removed.txt");
  await rm(path.join(fixture.rootPath, "removed.txt"));
  await fixture.write(".gitignore", "ignored/\n");
  await fixture.write("ignored/file.txt", "ignored\n");
  const indexPath = path.join(fixture.rootPath, ".git", "index");
  const before = await readFile(indexPath);

  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });
  assert.equal(snapshot.headRevision, null);
  assert.equal(snapshot.baseRevision, null);
  assert.equal(snapshot.currentBranch, "main");
  assert.deepEqual(snapshot.changedFiles.map((file) => file.newPath).sort(), [".gitignore", "README.md", "staged.ts"]);
  assert.ok(snapshot.changedFiles.every((file) => file.status === "added" && file.oldPath === null));
  const changed = snapshot.changedFiles.find((file) => file.newPath === "staged.ts");
  const diff = await getFileDiff(snapshot.snapshotId, changed.id);
  assert.equal(diff.oldText, null);
  assert.equal(diff.newText, "export const staged = false;\n");
  assert.equal(diff.supported, true);
  assert.deepEqual(await readFile(indexPath), before);
  assert.equal(await isReviewStale(snapshot.snapshotId), false);
  fixture.commit("first commit");
  assert.equal(await isReviewStale(snapshot.snapshotId), true);
});

test("an empty unborn repository opens normally and branch comparison explains the missing HEAD", async (t) => {
  const fixture = await createFixtureRepository({ initialCommit: false });
  t.after(fixture.cleanup);
  await rm(path.join(fixture.rootPath, "README.md"));
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });
  assert.deepEqual(snapshot.changedFiles, []);
  assert.deepEqual(snapshot.tree, []);
  await assert.rejects(stat(path.join(fixture.rootPath, ".git", "index")), { code: "ENOENT" });
  await assert.rejects(getReview(fixture.rootPath, { kind: "branch", baseRef: "refs/heads/main" }), /first commit/);
});

test("worktree snapshot combines staged, unstaged, and untracked changes and captures the full tree", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);

  await fixture.write("staged.txt", "staged\n");
  fixture.git("add", "staged.txt");
  await fixture.write("README.md", "unstaged\n");
  await fixture.write("new file-你好.txt", "untracked\n");
  await fixture.write(".hidden", "hidden\n");
  await fixture.write("ignored.tmp", "ignored\n");
  await fixture.write(".gitignore", "ignored.tmp\n");

  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });
  const paths = snapshot.changedFiles.map((file) => file.newPath ?? file.oldPath);

  assert.ok(paths.includes("README.md"));
  assert.ok(paths.includes("staged.txt"));
  assert.ok(paths.includes("new file-你好.txt"));
  assert.ok(snapshot.tree.some((entry) => entry.path === ".hidden"));
  assert.ok(snapshot.tree.some((entry) => entry.path === "ignored.tmp"));
  assert.equal(snapshot.baseRevision, fixture.baseRevision);
  assert.equal(await isReviewStale(snapshot.snapshotId), false);
});

test("branch snapshot diffs merge-base to current HEAD for a remote-tracking ref", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);

  fixture.git("switch", "-c", "feature");
  await fixture.write("feature.txt", "feature\n");
  const headRevision = fixture.commit("feature change");

  fixture.git("switch", "-c", "upstream", fixture.baseRevision);
  await fixture.write("upstream.txt", "upstream\n");
  const upstreamRevision = fixture.commit("upstream change");
  fixture.git("update-ref", "refs/remotes/origin/upstream", upstreamRevision);
  fixture.git("switch", "feature");

  const snapshot = await getReview(fixture.rootPath, {
    kind: "branch",
    baseRef: "refs/remotes/origin/upstream",
  });
  const paths = snapshot.changedFiles.map((file) => file.newPath ?? file.oldPath);

  assert.equal(snapshot.baseRevision, fixture.baseRevision);
  assert.equal(snapshot.headRevision, headRevision);
  assert.deepEqual(paths, ["feature.txt"]);
  assert.ok(snapshot.tree.some((entry) => entry.path === "feature.txt" && entry.changed));
  assert.ok(!snapshot.tree.some((entry) => entry.path === "upstream.txt"));
});

test("tree retains a deleted file path so the changed file remains selectable", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  await fixture.write("removed.txt", "remove me\n");
  const withFileRevision = fixture.commit("add file to remove");
  fixture.git("switch", "-c", "feature");
  fixture.git("rm", "removed.txt");
  fixture.commit("delete file");
  fixture.git("branch", "base", withFileRevision);

  const snapshot = await getReview(fixture.rootPath, {
    kind: "branch",
    baseRef: "refs/heads/base",
  });

  assert.equal(snapshot.changedFiles[0].status, "deleted");
  assert.ok(snapshot.tree.some((entry) => entry.path === "removed.txt" && entry.changed));
});

test("keeps the opened snapshot stable and reports a newer worktree state", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });

  await writeFile(path.join(fixture.rootPath, "after-open.txt"), "new\n");

  assert.equal(await isReviewStale(snapshot.snapshotId), true);
  assert.deepEqual(snapshot.changedFiles, []);
  assert.ok(!snapshot.tree.some((entry) => entry.path === "after-open.txt"));
});

test("reports a newer state after HEAD or a branch ref moves", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  fixture.git("branch", "moving");
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });

  await fixture.write("next.txt", "next\n");
  const newRevision = fixture.commit("advance main");
  fixture.git("update-ref", "refs/heads/moving", newRevision);

  assert.equal(await isReviewStale(snapshot.snapshotId), true);
});

test("detects another edit when Git status, file size, and mtime remain the same", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  const filePath = await fixture.write("README.md", "first edit\n");
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });
  const metadata = await stat(filePath);
  await writeFile(filePath, "other edit\n");
  await utimes(filePath, metadata.atime, metadata.mtime);
  assert.equal(await isReviewStale(snapshot.snapshotId), true);
});

test("reports a stale snapshot when the active branch changes at the same revision", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  fixture.git("branch", "same-revision");
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });

  fixture.git("switch", "same-revision");

  assert.equal(await isReviewStale(snapshot.snapshotId), true);
});

test("does not write Git index data while capturing a review", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  const indexPath = path.join(fixture.rootPath, ".git", "index");
  const before = await readFile(indexPath);

  await getReview(fixture.rootPath, { kind: "worktree" });

  assert.deepEqual(await readFile(indexPath), before);
});

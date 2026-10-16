import assert from "node:assert/strict";
import { mkdtemp, rename, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { getFileDiff } from "../src/server/diff.ts";
import { getFileView, getReview } from "../src/server/snapshot.ts";
import { createFixtureRepository } from "./fixtures/git-repo.mjs";

test("untracked and ignored files open as readonly source while tracked modifications remain diffs", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  await fixture.write("README.md", "modified\n");
  await fixture.write("new.ts", "export const value = 1;\n");
  await fixture.write(".gitignore", "ignored/\n");
  await fixture.write("ignored/source.ts", "ignored source\n");
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });
  const added = await getFileView(snapshot.snapshotId, "new.ts");
  assert.equal(added.mode, "file");
  assert.equal(added.text, "export const value = 1;\n");
  assert.equal(added.source, "worktree");
  const ignored = await getFileView(snapshot.snapshotId, "ignored/source.ts");
  assert.equal(ignored.mode, "file");
  assert.equal(ignored.text, "ignored source\n");
  const modified = await getFileView(snapshot.snapshotId, "README.md");
  assert.equal(modified.mode, "diff");
  assert.equal(modified.fileDiff.oldText, "base\n");
});

test("unchanged files are readable and a first-open capture stays stable until refresh", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });
  const first = await getFileView(snapshot.snapshotId, "README.md");
  assert.equal(first.mode, "file");
  assert.equal(first.text, "base\n");
  await fixture.write("README.md", "later\n");
  assert.deepEqual(await getFileView(snapshot.snapshotId, "README.md"), first);
  await assert.rejects(getFileView(snapshot.snapshotId, "../outside.txt"), /not part of this review/);
  await assert.rejects(getFileView(snapshot.snapshotId, ".git/config"), /not part of this review/);
});

test("branch source views read committed HEAD rather than uncommitted working-tree changes", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  fixture.git("branch", "base");
  await fixture.write("added.ts", "committed addition\n");
  fixture.commit("add file");
  await fixture.write("README.md", "uncommitted\n");
  await fixture.write("local.txt", "local only\n");
  const snapshot = await getReview(fixture.rootPath, { kind: "branch", baseRef: "refs/heads/base" });
  const unchanged = await getFileView(snapshot.snapshotId, "README.md");
  assert.equal(unchanged.mode, "file");
  assert.equal(unchanged.source, "revision");
  assert.equal(unchanged.text, "base\n");
  assert.equal((await getFileView(snapshot.snapshotId, "added.ts")).mode, "diff");
  assert.equal((await getFileView(snapshot.snapshotId, "local.txt")).source, "worktree");
});

test("readonly binary and oversized files return an explanation instead of content", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  await fixture.write(".gitignore", "ignored/\n");
  await fixture.write("ignored/binary", Buffer.from([0, 1, 2]));
  await fixture.write("ignored/large", "x".repeat(1024 * 1024 + 1));
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });
  for (const name of ["binary", "large"]) {
    const view = await getFileView(snapshot.snapshotId, `ignored/${name}`);
    assert.equal(view.mode, "file");
    assert.equal(view.supported, false);
    assert.equal(view.text, null);
  }
});

test("readonly file access does not follow a directory replaced by an outside symlink", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  const outside = await mkdtemp(path.join(tmpdir(), "codex-review-outside-"));
  t.after(() => rm(outside, { recursive: true, force: true }));
  await fixture.write("folder/file.txt", "inside\n");
  fixture.commit("add file");
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });
  await writeFile(path.join(outside, "file.txt"), "outside\n");
  await rename(path.join(fixture.rootPath, "folder"), path.join(fixture.rootPath, "original-folder"));
  await symlink(outside, path.join(fixture.rootPath, "folder"), process.platform === "win32" ? "junction" : "dir");
  const view = await getFileView(snapshot.snapshotId, "folder/file.txt");
  assert.equal(view.supported, false);
  assert.equal(view.text, null);
  assert.match(view.unsupportedReason, /outside/);
});

test("returns captured old and new text even after the worktree changes", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  await fixture.write("README.md", "new version\n");
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });
  await fixture.write("README.md", "later version\n");

  const changedFile = snapshot.changedFiles.find((file) => file.newPath === "README.md");
  const diff = await getFileDiff(snapshot.snapshotId, changedFile.id);

  assert.equal(diff.oldText, "base\n");
  assert.equal(diff.newText, "new version\n");
  assert.equal(diff.supported, true);
});

test("reads Unicode, spaces, and leading-dash filenames without changing their paths", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  await fixture.write("space and 你好.ts", "const value = 1;\n");
  await fixture.write("-leading-dash.txt", "safe path\n");
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });

  for (const relativePath of ["space and 你好.ts", "-leading-dash.txt"]) {
    const changedFile = snapshot.changedFiles.find((file) => file.newPath === relativePath);
    assert.ok(changedFile, `missing changed path ${relativePath}`);
    const diff = await getFileDiff(snapshot.snapshotId, changedFile.id);
    assert.equal(diff.newPath, relativePath);
    assert.equal(diff.supported, true);
  }
});

test("captures branch diff contents from merge-base and current HEAD", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  fixture.git("switch", "-c", "feature");
  await fixture.write("README.md", "feature revision\n");
  fixture.commit("feature edit");
  const snapshot = await getReview(fixture.rootPath, {
    kind: "branch",
    baseRef: "refs/heads/main",
  });

  const changedFile = snapshot.changedFiles.find((file) => file.newPath === "README.md");
  const diff = await getFileDiff(snapshot.snapshotId, changedFile.id);

  assert.equal(diff.oldText, "base\n");
  assert.equal(diff.newText, "feature revision\n");
});

test("preserves rename and deletion statuses with the correct old and new sides", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  await fixture.write("deleted.txt", "delete me\n");
  fixture.commit("add deletable file");
  fixture.git("mv", "README.md", "renamed file-你好.md");
  fixture.git("rm", "deleted.txt");
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });

  const rename = snapshot.changedFiles.find((file) => file.status === "renamed");
  const deletion = snapshot.changedFiles.find((file) => file.status === "deleted");
  assert.equal(rename.oldPath, "README.md");
  assert.equal(rename.newPath, "renamed file-你好.md");
  assert.equal((await getFileDiff(snapshot.snapshotId, rename.id)).newText, "base\n");
  assert.equal(deletion.oldPath, "deleted.txt");
  assert.equal(deletion.newPath, null);
  assert.equal((await getFileDiff(snapshot.snapshotId, deletion.id)).newText, null);
  assert.equal((await getFileView(snapshot.snapshotId, deletion.oldPath)).mode, "diff");
  assert.equal((await getFileView(snapshot.snapshotId, rename.newPath)).mode, "diff");
});

test("identifies binary content explicitly", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  await writeFile(path.join(fixture.rootPath, "binary.bin"), Buffer.from([0, 1, 2, 3]));
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });
  const changedFile = snapshot.changedFiles.find((file) => file.newPath === "binary.bin");

  const diff = await getFileDiff(snapshot.snapshotId, changedFile.id);

  assert.equal(diff.supported, false);
  assert.match(diff.unsupportedReason, /binary/i);
});

test("identifies oversized files without returning their content", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  await writeFile(path.join(fixture.rootPath, "large.txt"), "x".repeat(1024 * 1024 + 1));
  const snapshot = await getReview(fixture.rootPath, { kind: "worktree" });
  const changedFile = snapshot.changedFiles.find((file) => file.newPath === "large.txt");

  const diff = await getFileDiff(snapshot.snapshotId, changedFile.id);

  assert.equal(diff.supported, false);
  assert.equal(diff.newText, null);
  assert.match(diff.unsupportedReason, /1 MiB/i);
});

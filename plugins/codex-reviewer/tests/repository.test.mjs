import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { listRepositories } from "../src/server/repository.ts";
import { createFixtureRepository } from "./fixtures/git-repo.mjs";
import { getFileView, getReview } from "../src/server/snapshot.ts";

test("reviews only the selected linked worktree, including detached HEAD", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  const linked = path.join(fixture.rootPath, "linked");
  fixture.git("worktree", "add", "--detach", linked, "HEAD");
  assert.match(await readFile(path.join(linked, ".git"), "utf8"), /^gitdir:/);
  await fixture.write("README.md", "main worktree only\n");
  await writeFile(path.join(linked, "README.md"), "linked worktree only\n");
  const review = await getReview(linked, { kind: "worktree" });
  assert.equal(review.rootPath, await realpath(linked));
  assert.equal(review.currentBranch, null);
  assert.equal(review.headRevision, fixture.baseRevision);
  assert.deepEqual(review.changedFiles.map((item) => item.newPath), ["README.md"]);
  assert.equal(review.tree.some((item) => item.path === ".git"), false);
  const view = await getFileView(review.snapshotId, "README.md");
  assert.equal(view.mode, "diff");
  assert.equal(view.fileDiff.oldText, "base\n");
  assert.equal(view.fileDiff.newText, "linked worktree only\n");
});

test("resolves a selected nested folder to its Git worktree and exposes local and remote refs", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  await mkdir(path.join(fixture.rootPath, "src", "nested"), { recursive: true });
  fixture.git("branch", "topic");
  fixture.git("update-ref", "refs/remotes/origin/topic", fixture.baseRevision);

  const repository = await listRepositories(path.join(fixture.rootPath, "src", "nested"));

  assert.equal(repository.rootPath, fixture.rootPath);
  assert.ok(repository.refs.some((ref) => ref.fullName === "refs/heads/topic"));
  assert.ok(repository.refs.some((ref) => ref.fullName === "refs/remotes/origin/topic"));
});

test("rejects a path outside any Git worktree with a clear error", async (t) => {
  const folder = await mkdtemp(path.join(tmpdir(), "codex-review-non-git-"));
  t.after(() => rm(folder, { recursive: true, force: true }));
  await assert.rejects(
    listRepositories(folder),
    /not a Git worktree/i,
  );
});

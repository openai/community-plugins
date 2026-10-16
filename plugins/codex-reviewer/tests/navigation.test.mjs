import assert from "node:assert/strict";
import test from "node:test";
import * as tree from "../src/app/FileTree.tsx";
import * as api from "../src/app/api.ts";
import { getFileView, getReview } from "../src/server/snapshot.ts";
import { createFixtureRepository } from "./fixtures/git-repo.mjs";

test("orders changed file navigation like the tree and uses the new path of a rename", () => {
  assert.equal(typeof tree.orderedChangedPaths, "function");
  const files = [
    { oldPath: "old.ts", newPath: "z.ts" },
    { oldPath: null, newPath: "a.ts" },
    { oldPath: "src/old.ts", newPath: null },
    { oldPath: null, newPath: "src/nested/change.ts" },
  ];
  assert.deepEqual(tree.orderedChangedPaths(files), ["src/nested/change.ts", "src/old.ts", "a.ts", "z.ts"]);
});

test("crosses files in both directions and wraps past readonly, binary and unchanged rename views", async (t) => {
  assert.equal(typeof api.readAdjacentDiff, "function");
  assert.equal(typeof tree.orderedChangedPaths, "function");
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  fixture.git("config", "core.autocrlf", "false");
  await fixture.write("a.ts", "before a\n");
  await fixture.write("b.bin", "before binary\n");
  await fixture.write("c.txt", "unchanged rename\n");
  await fixture.write("eol.txt", "same line\n");
  await fixture.write("z.ts", "before z\n");
  fixture.commit("navigation base");
  await fixture.write("a.ts", "after a\n");
  await fixture.write("b.bin", "binary\0data");
  fixture.git("mv", "c.txt", "d.txt");
  await fixture.write("eol.txt", "same line\r\n");
  await fixture.write("notes.txt", "untracked source\n");
  await fixture.write("z.ts", "after z\n");
  const review = await getReview(fixture.rootPath, { kind: "worktree" });
  const paths = tree.orderedChangedPaths(review.changedFiles);
  const app = { callServerTool: async ({ arguments: args }) => ({ structuredContent: await getFileView(args.snapshotId, args.path) }) };
  for (const [from, direction, expected] of [["a.ts", 1, "z.ts"], ["z.ts", -1, "a.ts"], ["z.ts", 1, "a.ts"], ["a.ts", -1, "z.ts"]]) {
    const next = await api.readAdjacentDiff(app, review.snapshotId, paths, from, direction);
    assert.equal(next.path, expected);
    assert.equal(next.view.mode, "diff");
    assert.equal(next.view.fileDiff.newText, expected === "a.ts" ? "after a\n" : "after z\n");
  }
  assert.equal(await api.readAdjacentDiff(app, review.snapshotId, ["b.bin", "d.txt", "eol.txt", "notes.txt"], "d.txt", 1), null);
  assert.equal(await api.readAdjacentDiff(app, review.snapshotId, [], "a.ts", 1), null);
  assert.equal((await api.readAdjacentDiff(app, review.snapshotId, ["a.ts"], "a.ts", 1)).path, "a.ts");
});

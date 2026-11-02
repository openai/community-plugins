import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createFixtureRepository } from "../../../plugins/codex-reviewer/tests/fixtures/git-repo.mjs";
import { connect } from "../support/mcp-client.mjs";

test("MCP rejects escaped paths, invented snapshots and option-like branches without modifying Git", async (t) => {
  const fixture = await createFixtureRepository();
  t.after(fixture.cleanup);
  const index = path.join(fixture.rootPath, ".git/index");
  const before = await readFile(index);
  const status = fixture.git("status", "--porcelain");
  const client = await connect(fileURLToPath(new URL("../../../plugins/codex-reviewer/", import.meta.url)));
  try {
    const review = await client.call("code-review.getReview", { rootPath: fixture.rootPath, scope: { kind: "worktree" } });
    assert.notEqual(review.isError, true);
    const { snapshotId } = review.structuredContent;
    for (const file of ["../outside.txt", ".git/config", path.resolve(fixture.rootPath, "README.md")]) {
      const result = await client.call("code-review.getFile", { snapshotId, path: file });
      assert.equal(result.isError, true);
      assert.ok(!result.structuredContent);
    }
    const invented = await client.call("code-review.getFileDiff", { snapshotId: "not-a-snapshot", fileId: "not-a-file" });
    assert.equal(invented.isError, true);
    const invalid = await client.call("code-review.getReview", { rootPath: fixture.rootPath, scope: { kind: "branch", baseRef: "--output=unexpected" } });
    assert.equal(invalid.isError, true);
    assert.deepEqual(await readFile(index), before);
    assert.equal(fixture.git("status", "--porcelain"), status);
  } finally { await client.close(); }
});

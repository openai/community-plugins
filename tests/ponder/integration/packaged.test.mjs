import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const sourcePlugin = path.join(repoRoot, "plugins", "ponder");

function walk(root) {
  const entries = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const file = path.join(root, entry.name);
    entries.push(file);
    if (entry.isDirectory()) {
      entries.push(...walk(file));
    }
  }
  return entries;
}

test("copied package runs without the parent checkout or node_modules", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "ponder-package-"));
  const copiedPlugin = path.join(tempRoot, "ponder");
  const unrelatedCwd = path.join(tempRoot, "unrelated-cwd");
  try {
    fs.cpSync(sourcePlugin, copiedPlugin, {
      recursive: true,
      filter: (source) => path.basename(source) !== "node_modules",
    });
    fs.mkdirSync(unrelatedCwd);

    for (const file of walk(copiedPlugin)) {
      const relative = path.relative(copiedPlugin, file);
      assert.equal(fs.lstatSync(file).isSymbolicLink(), false, "symlink in package: " + relative);
      assert.doesNotMatch(relative, /(^|\/)node_modules(\/|$)/);
    }

    const cli = path.join(copiedPlugin, "scripts", "ponder.mjs");
    const fixture = path.join(copiedPlugin, "fixtures", "synthetic", "sample-audit");

    const inspect = spawnSync(
      process.execPath,
      [cli, "inspect", "--session-dir", fixture, "--format", "json"],
      { cwd: unrelatedCwd, encoding: "utf8" },
    );
    assert.equal(inspect.status, 0, inspect.stderr);
    const inspectBody = JSON.parse(inspect.stdout);
    assert.equal(inspectBody.readOnly, true);
    assert.equal(inspectBody.writesPerformed, false);
    assert.equal(inspectBody.canStop, true);
    assert.equal(inspectBody.verdict, "converged");

    const verify = spawnSync(
      process.execPath,
      [cli, "verify", "--session-dir", fixture, "--format", "json"],
      { cwd: unrelatedCwd, encoding: "utf8" },
    );
    assert.equal(verify.status, 0, verify.stderr);
    const verifyBody = JSON.parse(verify.stdout);
    assert.equal(verifyBody.ok, true);
    assert.equal(verifyBody.coverage.ratio, 1);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

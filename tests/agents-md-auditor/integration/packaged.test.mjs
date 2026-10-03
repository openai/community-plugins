import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { auditJson, pluginDir, walk } from "../support/helpers.mjs";

test("copied package runs without the parent checkout or node_modules", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "agents-md-auditor-package-"));
  const copied = path.join(tempRoot, "agents-md-auditor");
  const unrelatedCwd = path.join(tempRoot, "unrelated-cwd");
  try {
    fs.cpSync(pluginDir, copied, { recursive: true });
    fs.mkdirSync(unrelatedCwd);
    for (const file of walk(copied)) {
      assert.equal(fs.lstatSync(file).isSymbolicLink(), false, "symlink in package: " + path.relative(copied, file));
    }
    const cli = path.join(copied, "scripts", "agents-md-audit.mjs");
    const healthy = auditJson(path.join(copied, "fixtures", "synthetic", "healthy"), [], { cli, cwd: unrelatedCwd });
    assert.equal(healthy.status, 0, healthy.stderr);
    assert.equal(healthy.report.ok, true);
    const stale = auditJson(path.join(copied, "fixtures", "synthetic", "stale"), [], { cli, cwd: unrelatedCwd });
    assert.equal(stale.status, 1);
    assert.equal(stale.report.summary.error, 3);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

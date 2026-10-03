import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { auditJson, fixtures, ids, runCli, treeDigest } from "../support/helpers.mjs";

test("healthy fixture has no findings and exits 0", () => {
  const { status, report } = auditJson(path.join(fixtures, "healthy"));
  assert.equal(status, 0);
  assert.equal(report.ok, true);
  assert.equal(report.readOnly, true);
  assert.equal(report.writesPerformed, false);
  assert.deepEqual(report.summary, { files: 1, error: 0, warning: 0, info: 0 });
  assert.deepEqual(report.files, [{ path: "AGENTS.md", bytes: report.files[0].bytes, effective: true }]);
});

test("stale fixture reports each seeded problem exactly once", () => {
  const { status, report } = auditJson(path.join(fixtures, "stale"));
  assert.equal(status, 1);
  assert.equal(report.ok, false);
  assert.deepEqual(ids(report), [
    "package-manager-mismatch@AGENTS.md:6",
    "unknown-script@AGENTS.md:7",
    "unknown-make-target@AGENTS.md:8",
    "broken-path@AGENTS.md:13",
    "broken-link@AGENTS.md:15",
    "duplicate-heading@AGENTS.md:17",
    "vague-instruction@AGENTS.md:19",
  ]);
  assert.deepEqual(report.summary, { files: 1, error: 3, warning: 3, info: 1 });
  const unknownScript = report.findings.find((finding) => finding.id === "unknown-script");
  assert.match(unknownScript.message, /available: build, test/);
  const makeTarget = report.findings.find((finding) => finding.id === "unknown-make-target");
  assert.match(makeTarget.message, /in Makefile\.$/);
});

test("monorepo fixture resolves nearest package.json, overrides, cd, and parent repetition", () => {
  const { status, report } = auditJson(path.join(fixtures, "monorepo"));
  assert.equal(status, 1);
  assert.deepEqual(ids(report), [
    "unknown-script@packages/api/AGENTS.md:4",
    "repeated-from-parent@packages/api/AGENTS.md:5",
    "unknown-script@packages/web/AGENTS.md:3",
    "override-shadows@packages/web/AGENTS.override.md:1",
  ]);
  const effective = Object.fromEntries(report.files.map((file) => [file.path, file.effective]));
  assert.deepEqual(effective, {
    "AGENTS.md": true,
    "packages/api/AGENTS.md": true,
    "packages/web/AGENTS.md": false,
    "packages/web/AGENTS.override.md": true,
  });
  const migrate = report.findings[0];
  assert.match(migrate.message, /packages\/api\/package\.json/, "nearest package.json wins over the root");
});

test("text output is grouped by file and states that nothing was written", () => {
  const { status, stdout } = runCli(["audit", "--root", path.join(fixtures, "stale")]);
  assert.equal(status, 1);
  assert.match(stdout, /^AGENTS\.md audit: 1 file, 3 errors, 3 warnings, 1 info \(read-only; no files written\)$/m);
  assert.match(stdout, /^AGENTS\.md$/m);
  assert.match(stdout, /^\s+7\s+error\s+unknown-script\s+/m);
});

test("auditing never modifies the audited tree", () => {
  for (const name of ["healthy", "stale", "monorepo"]) {
    const root = path.join(fixtures, name);
    const before = treeDigest(root);
    auditJson(root);
    runCli(["audit", "--root", root]);
    assert.equal(treeDigest(root), before, name + " changed during audit");
  }
});

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const sourcePlugin = path.join(repoRoot, "plugins", "la-review");
const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");

function run(command, args, cwd) {
  return spawnSync(command, args, {
    cwd,
    encoding: "utf8",
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  });
}

test("copied package captures a bounded working-tree diff without the parent checkout", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "la-review-package-"));
  const copiedPlugin = path.join(tempRoot, "la-review");
  const fixture = path.join(tempRoot, "fixture repository");
  try {
    fs.cpSync(sourcePlugin, copiedPlugin, { recursive: true });
    fs.mkdirSync(fixture);

    for (const args of [
      ["init", "--quiet"],
      ["config", "user.email", "reviewer@example.invalid"],
      ["config", "user.name", "Review Fixture"],
    ]) {
      const result = run("git", args, fixture);
      assert.equal(result.status, 0, result.stderr);
    }

    const source = path.join(fixture, "example.py");
    fs.writeFileSync(source, "value = 1\n");
    for (const args of [["add", "example.py"], ["commit", "--quiet", "-m", "fixture"]]) {
      const result = run("git", args, fixture);
      assert.equal(result.status, 0, result.stderr);
    }
    fs.writeFileSync(source, "value = 2\n");

    const output = path.join(tempRoot, "review.diff");
    const collector = path.join(
      copiedPlugin,
      "skills",
      "loupe",
      "scripts",
      "collect_review_diff.py",
    );
    const result = run(
      python,
      [
        collector,
        "uncommitted changes (staged + unstaged + untracked)",
        "--output",
        output,
      ],
      fixture,
    );
    assert.equal(result.status, 0, result.stderr);
    const diff = fs.readFileSync(output, "utf8");
    assert.match(diff, /diff --git a\/example\.py b\/example\.py/);
    assert.match(diff, /\+value = 2/);
    assert.doesNotMatch(diff, /plugins\/la-review/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

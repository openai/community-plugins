import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");

test("cold copied runner handles tools, scopes, failures, timeouts, and output offline", { skip: process.platform === "win32" }, () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "la-review-runner-"));
  const copiedPlugin = path.join(tempRoot, "la-review");
  try {
    fs.cpSync(path.join(repoRoot, "plugins", "la-review"), copiedPlugin, { recursive: true });
    const result = spawnSync(python, [path.join(repoRoot, "tests", "la-review", "support", "runner_contract.py")], {
      cwd: tempRoot,
      encoding: "utf8",
      timeout: 20000,
      env: {
        ...process.env,
        PYTHONDONTWRITEBYTECODE: "1",
        LOUPE_TEST_RUNNER: path.join(copiedPlugin, "skills", "loupe", "scripts", "run_reviewers.py"),
      },
    });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

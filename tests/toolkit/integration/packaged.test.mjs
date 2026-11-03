import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const sourcePlugin = path.join(repoRoot, "plugins", "toolkit");
const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");

function run(script, args, cwd) {
  return spawnSync(python, [script, ...args], {
    cwd,
    encoding: "utf8",
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  });
}

test("copied package lists, inspects, and renders actions without the parent checkout", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "toolkit-package-"));
  const copiedPlugin = path.join(tempRoot, "toolkit");
  const unrelatedCwd = path.join(tempRoot, "unrelated directory");
  try {
    fs.cpSync(sourcePlugin, copiedPlugin, { recursive: true });
    fs.mkdirSync(unrelatedCwd);
    const scripts = path.join(copiedPlugin, "skills", "perform", "scripts");
    const listScript = path.join(scripts, "list_perform_actions.py");
    const getScript = path.join(scripts, "get_perform_action.py");

    const listed = run(listScript, ["--cwd", unrelatedCwd], unrelatedCwd);
    assert.equal(listed.status, 0, listed.stderr);
    const variants = JSON.parse(listed.stdout).variants;
    assert.ok(variants.some((variant) => variant.selector === "find-todos[agnostic]"));
    assert.ok(variants.some((variant) => variant.selector === "audit-project-setup[python]"));

    const inspected = run(
      getScript,
      ["--inspect", "find-todos[agnostic]", "--cwd", unrelatedCwd],
      unrelatedCwd,
    );
    assert.equal(inspected.status, 0, inspected.stderr);
    assert.equal(JSON.parse(inspected.stdout).mode, "default");

    const missing = run(
      getScript,
      ["--render", "check-cross-platform[agnostic]", "--cwd", unrelatedCwd],
      unrelatedCwd,
    );
    assert.notEqual(missing.status, 0);
    assert.equal(JSON.parse(missing.stdout).error.code, "missing_variables");

    const rendered = run(
      getScript,
      [
        "--render",
        "check-cross-platform[agnostic]",
        "--var",
        "OSList=Ubuntu 18.04 and macOS 14",
        "--cwd",
        unrelatedCwd,
      ],
      unrelatedCwd,
    );
    assert.equal(rendered.status, 0, rendered.stderr);
    assert.match(JSON.parse(rendered.stdout).prompt, /Ubuntu 18\.04 and macOS 14/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

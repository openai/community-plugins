import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const pluginDir = path.join(repoRoot, "plugins", "ponder");
const cli = path.join(pluginDir, "scripts", "ponder.mjs");

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

test("distribution has no symlinks, caches, generated artifacts, or dependencies", () => {
  for (const file of walk(pluginDir)) {
    const relative = path.relative(pluginDir, file);
    const stats = fs.lstatSync(file);
    assert.equal(stats.isSymbolicLink(), false, "symlink rejected: " + relative);
    assert.doesNotMatch(
      relative,
      /(^|\/)(node_modules|\.cache|__pycache__|\.DS_Store|dist|build)(\/|$)/,
    );
    if (!stats.isDirectory()) {
      assert.ok(stats.isFile(), "only regular files are packaged: " + relative);
    }
  }
});

test("public plugin contains no private paths or secret-shaped material", () => {
  const forbidden = [
    /\/Users\//,
    /\/home\/[A-Za-z0-9._-]+\//,
    /\bfile:\/\//i,
    /(?:ghp_|github_pat_|sk-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._-]{20,})/,
    /(?:password|secret|token|api[_-]?key)\s*[:=]\s*["'][^"']+["']/i,
    /https?:\/\/[^\s"')]+(?:\.internal|\.local|privatelink)/i,
  ];

  for (const file of walk(pluginDir)) {
    if (!fs.lstatSync(file).isFile()) {
      continue;
    }
    const text = fs.readFileSync(file, "utf8");
    for (const pattern of forbidden) {
      assert.doesNotMatch(text, pattern, "forbidden material in " + path.relative(pluginDir, file));
    }
  }
});

test("ponder CLI rejects report path traversal outside --session-dir and rejects symlinks", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "ponder-sec-"));
  const fixture = path.join(pluginDir, "fixtures", "synthetic", "sample-audit");
  const sessionCopy = path.join(tempRoot, "session");
  try {
    fs.cpSync(fixture, sessionCopy, { recursive: true });

    const escapeAttempt = spawnSync(
      process.execPath,
      [cli, "report", "--session-dir", sessionCopy, "--output", "../escaped.md"],
      { encoding: "utf8" },
    );
    assert.equal(escapeAttempt.status, 2);
    assert.equal(JSON.parse(escapeAttempt.stderr).code, "PATH_OUTSIDE_SESSION_DIR");
    assert.equal(fs.existsSync(path.join(tempRoot, "escaped.md")), false);

    const symlinkSession = path.join(tempRoot, "symlink-session");
    fs.symlinkSync(sessionCopy, symlinkSession);
    const symlinkAttempt = spawnSync(
      process.execPath,
      [cli, "inspect", "--session-dir", symlinkSession],
      { encoding: "utf8" },
    );
    assert.equal(symlinkAttempt.status, 2);
    assert.equal(JSON.parse(symlinkAttempt.stderr).code, "SYMLINK_REJECTED");
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

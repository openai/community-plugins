import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { auditJson, ids, pluginDir, runCli, walk, withTempRepo } from "../support/helpers.mjs";

const secretValuePatterns = [
  /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}/,
  /\bgithub_pat_[A-Za-z0-9_]{40,}/,
  /\bsk-[A-Za-z0-9_-]{32,}/,
  /\bAKIA[0-9A-Z]{16}\b/,
  /\bxox[abprs]-[A-Za-z0-9-]{20,}/,
  /-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----/,
];

test("distribution has no symlinks, caches, dependencies, or generated artifacts", () => {
  for (const file of walk(pluginDir)) {
    const relative = path.relative(pluginDir, file);
    const stats = fs.lstatSync(file);
    assert.equal(stats.isSymbolicLink(), false, "symlink rejected: " + relative);
    assert.doesNotMatch(relative, /(^|\/)(node_modules|\.cache|__pycache__|\.DS_Store|dist|build|coverage)(\/|$)/);
    if (!stats.isDirectory()) assert.ok(stats.isFile(), "only regular files are packaged: " + relative);
  }
  assert.equal(fs.existsSync(path.join(pluginDir, "package.json")), false, "package must stay dependency-free");
});

test("public plugin contains no personal paths, secret values, or private hosts", () => {
  for (const file of walk(pluginDir)) {
    if (!fs.lstatSync(file).isFile()) continue;
    const text = fs.readFileSync(file, "utf8");
    const relative = path.relative(pluginDir, file);
    assert.doesNotMatch(text, /\/Users\/|\/home\/[a-z]/, "personal path in " + relative);
    assert.doesNotMatch(text, /https?:\/\/[^\s"')]+(?:\.internal|\.local|privatelink)/i, "private host in " + relative);
    for (const pattern of secretValuePatterns) {
      assert.doesNotMatch(text, pattern, "secret-shaped value in " + relative);
    }
  }
});

test("checker source has no network, process-spawning, or write APIs", () => {
  const source = fs.readFileSync(path.join(pluginDir, "scripts", "agents-md-audit.mjs"), "utf8");
  for (const forbidden of [
    /node:(?:http|https|net|tls|dgram|dns|child_process|worker_threads)/,
    /\bfetch\(/,
    /\b(?:writeFile|writeFileSync|appendFile|appendFileSync|mkdir|mkdirSync|rm|rmSync|unlink|unlinkSync|rename|renameSync|copyFile|copyFileSync|cpSync|createWriteStream|chmod|chmodSync)\b/,
    /\beval\(|new Function\(/,
  ]) {
    assert.doesNotMatch(source, forbidden);
  }
  assert.match(source, /fs\.openSync\(filePath, "r"\)/, "files are opened read-only");
});

test("credential-shaped values are reported but redacted in every output format", () => {
  const value = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8";
  withTempRepo({ "AGENTS.md": "# Setup\n\nexport GITHUB_TOKEN=" + value + "\n" }, (tmp) => {
    const json = auditJson(tmp);
    assert.equal(json.status, 1);
    assert.deepEqual(ids(json.report), ["secret-like@AGENTS.md:3"]);
    assert.ok(!json.stdout.includes(value), "JSON output leaked the value");
    assert.match(json.report.findings[0].message, /ghp_…\[redacted \d+ chars\]/);
    const text = runCli(["audit", "--root", tmp]);
    assert.ok(!text.stdout.includes(value), "text output leaked the value");
  });
});

test("symlinked directories and files are never followed", (t) => {
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "agents-md-auditor-outside-"));
  try {
    fs.writeFileSync(path.join(outside, "AGENTS.md"), "- `npm run outside-only`\n");
    withTempRepo({ "AGENTS.md": "# Root\n" }, (tmp) => {
      try {
        fs.symlinkSync(outside, path.join(tmp, "linked-dir"), "dir");
        fs.symlinkSync(path.join(outside, "AGENTS.md"), path.join(tmp, "sub-AGENTS-link"));
        fs.mkdirSync(path.join(tmp, "sub"));
        fs.symlinkSync(path.join(outside, "AGENTS.md"), path.join(tmp, "sub", "AGENTS.md"));
      } catch (error) {
        t.skip("symlinks unavailable on this host: " + error.code);
        return;
      }
      const { report } = auditJson(tmp);
      assert.deepEqual(report.files.map((file) => file.path), ["AGENTS.md"]);
      assert.equal(report.findings.length, 0);
    });
  } finally {
    fs.rmSync(outside, { recursive: true, force: true });
  }
});

test("references outside the root are reported without being inspected", () => {
  withTempRepo(
    { "repo/AGENTS.md": "- See `../../etc/hosts` and [parent](../../secret.md).\n" },
    (tmp) => {
      const { report } = auditJson(path.join(tmp, "repo"));
      assert.deepEqual(ids(report), ["path-outside-root@AGENTS.md:1", "path-outside-root@AGENTS.md:1"]);
      assert.ok(report.findings.every((finding) => finding.severity === "info"));
    },
  );
});

test("reports never contain absolute paths", () => {
  withTempRepo(
    { "AGENTS.md": "- `npm run missing`\n- `src/missing.js`\n", "package.json": "{\"scripts\":{}}" },
    (tmp) => {
      const json = auditJson(tmp);
      const text = runCli(["audit", "--root", tmp]);
      const real = fs.realpathSync(tmp);
      for (const output of [json.stdout, text.stdout]) {
        assert.ok(!output.includes(tmp) && !output.includes(real), "absolute path leaked");
      }
      assert.equal(json.report.root, ".");
    },
  );
});

test("audit skill is read-only and revise skill bounds writes to approved AGENTS files", () => {
  const audit = fs.readFileSync(path.join(pluginDir, "skills", "audit-agents-md", "SKILL.md"), "utf8");
  const revise = fs.readFileSync(path.join(pluginDir, "skills", "revise-agents-md", "SKILL.md"), "utf8");
  assert.match(audit, /read-only/);
  assert.match(audit, /Do not edit, create, delete, rename, or format any file\./);
  assert.match(audit, /Do not run the commands, scripts, or make targets/);
  assert.match(revise, /Write only to `AGENTS\.md` or `AGENTS\.override\.md` files inside the declared repository root\./);
  assert.match(revise, /Do not write anything until the user has approved the specific change\./);
  assert.match(revise, /Never add credentials/);
});

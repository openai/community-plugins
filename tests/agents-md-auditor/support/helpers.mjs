import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
export const pluginDir = path.join(repoRoot, "plugins", "agents-md-auditor");
export const cli = path.join(pluginDir, "scripts", "agents-md-audit.mjs");
export const fixtures = path.join(pluginDir, "fixtures", "synthetic");

export function runCli(args, options = {}) {
  const result = spawnSync(process.execPath, [options.cli ?? cli, ...args], {
    cwd: options.cwd ?? repoRoot,
    encoding: "utf8",
  });
  return { status: result.status, stdout: result.stdout, stderr: result.stderr };
}

export function auditJson(root, extra = [], options = {}) {
  const result = runCli(["audit", "--root", root, "--format", "json", ...extra], options);
  return { ...result, report: result.stdout ? JSON.parse(result.stdout) : null };
}

export function walk(root) {
  const entries = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const file = path.join(root, entry.name);
    entries.push(file);
    if (entry.isDirectory()) entries.push(...walk(file));
  }
  return entries;
}

export function treeDigest(root) {
  const hash = createHash("sha256");
  for (const file of walk(root).sort()) {
    const stat = fs.lstatSync(file);
    hash.update(path.relative(root, file) + "\0" + stat.mode + "\0" + stat.mtimeMs + "\0");
    if (stat.isFile()) hash.update(fs.readFileSync(file));
  }
  return hash.digest("hex");
}

export function withTempRepo(files, callback) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "agents-md-auditor-"));
  try {
    for (const [relative, content] of Object.entries(files)) {
      const target = path.join(root, relative);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, content);
    }
    return callback(root);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

export function ids(report) {
  return report.findings.map((finding) => finding.id + "@" + finding.file + ":" + finding.line);
}

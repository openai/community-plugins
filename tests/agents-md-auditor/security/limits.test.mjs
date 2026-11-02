import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  DEFAULT_MAX_DEPTH,
  discoverInstructionFiles,
} from "../../../plugins/agents-md-auditor/scripts/agents-md-audit.mjs";
import { auditJson, ids, withTempRepo } from "../support/helpers.mjs";

const MiB = 1024 * 1024;

function manyDirectories(count, prefix = "z") {
  const files = {};
  for (let index = 0; index < count; index += 1) {
    files[prefix + String(index).padStart(3, "0") + "/notes.txt"] = "x\n";
  }
  return files;
}

test("the walk stops as soon as the file limit is reached", () => {
  withTempRepo({ "AGENTS.md": "# Root\n", "a/AGENTS.md": "# A\n", ...manyDirectories(50) }, (tmp) => {
    const { report } = auditJson(tmp, ["--max-files", "1"]);
    assert.deepEqual(ids(report), ["discovery-limit@.:0"]);
    assert.match(report.findings[0].message, /instruction files/);
    assert.equal(report.discovery.limit, "files");
    assert.ok(report.discovery.directories <= 2, "walked " + report.discovery.directories + " directories after the file limit");
  });
});

test("--max-dirs bounds the number of directories listed", () => {
  withTempRepo({ "AGENTS.md": "# Root\n", ...manyDirectories(30), "zzz/AGENTS.md": "# Late\n" }, (tmp) => {
    const { report } = auditJson(tmp, ["--max-dirs", "5"]);
    assert.equal(report.discovery.directories, 5);
    assert.equal(report.discovery.limit, "directories");
    assert.deepEqual(report.files.map((file) => file.path), ["AGENTS.md"]);
    assert.ok(ids(report).includes("discovery-limit@.:0"));
    assert.match(report.findings.find((finding) => finding.id === "discovery-limit").message, /directories/);
  });
});

test("directories deeper than the depth limit are not listed", () => {
  const deep = Array.from({ length: DEFAULT_MAX_DEPTH + 2 }, (_, index) => "d" + index).join("/");
  withTempRepo({ "AGENTS.md": "# Root\n", [deep + "/AGENTS.md"]: "# Too deep\n" }, (tmp) => {
    const { report } = auditJson(tmp);
    assert.deepEqual(report.files.map((file) => file.path), ["AGENTS.md"]);
    assert.equal(report.discovery.limit, "depth");
    assert.equal(report.discovery.directories, DEFAULT_MAX_DEPTH + 1);
    assert.ok(ids(report).includes("discovery-limit@.:0"));
  });
});

test("the total number of directory entries examined is bounded", () => {
  withTempRepo({ "AGENTS.md": "# Root\n", ...manyDirectories(40) }, (tmp) => {
    const result = discoverInstructionFiles(tmp, { maxEntries: 10 });
    assert.equal(result.limit, "entries");
    assert.equal(result.entries, 10);
  });
});

test("unbounded trees without limits report no discovery limit", () => {
  withTempRepo({ "AGENTS.md": "# Root\n", ...manyDirectories(10) }, (tmp) => {
    const { report } = auditJson(tmp);
    assert.equal(report.discovery.limit, null);
    assert.equal(report.discovery.directories, 11);
    assert.deepEqual(ids(report), []);
  });
});

test("instruction files are read only up to 1 MiB", () => {
  const filler = ("x".repeat(99) + "\n").repeat(MiB / 100 + 100);
  withTempRepo(
    {
      "AGENTS.md": "- `npm run before-cap`\n" + filler + "- `npm run after-cap`\n",
      "package.json": "{\"scripts\":{}}",
    },
    (tmp) => {
      const { report } = auditJson(tmp, ["--fail-on", "never"]);
      const messages = report.findings.map((finding) => finding.message).join("\n");
      assert.match(messages, /before-cap/);
      assert.doesNotMatch(messages, /after-cap/, "content past the 1 MiB read cap was parsed");
      assert.ok(report.files[0].bytes > MiB, "reported size is the real size");
      assert.ok(ids(report).some((id) => id.startsWith("oversize-file@")));
    },
  );
});

test("package.json and Makefiles are read only up to 1 MiB", () => {
  const padding = " ".repeat(MiB);
  withTempRepo(
    {
      "AGENTS.md": "- `npm run build`\n- `make late`\n",
      "package.json": "{" + padding + "\"scripts\":{\"build\":\"x\"}}",
      Makefile: "# " + "x".repeat(MiB) + "\nlate:\n\techo\n",
    },
    (tmp) => {
      const { report } = auditJson(tmp, ["--fail-on", "never"]);
      // A truncated package.json does not parse and is skipped, never trusted.
      assert.ok(!ids(report).some((id) => id.startsWith("unknown-script@")));
      assert.ok(ids(report).some((id) => id.startsWith("unknown-make-target@")), "Makefile content past the cap was parsed");
    },
  );
});

function trySymlink(t, target, linkPath) {
  try {
    fs.symlinkSync(target, linkPath);
    return true;
  } catch (error) {
    if (process.platform !== "win32") throw error;
    t.skip("symlinks unavailable on this host: " + error.code);
    return false;
  }
}

test("symlinked package.json and Makefile are never followed", (t) => {
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "agents-md-auditor-outside-"));
  try {
    fs.writeFileSync(path.join(outside, "package.json"), "{\"scripts\":{\"outside-only\":\"x\"}}");
    fs.writeFileSync(path.join(outside, "Makefile"), "outside-only:\n\techo\n");
    withTempRepo({ "AGENTS.md": "- `npm run outside-only`\n- `make outside-only`\n" }, (tmp) => {
      if (!trySymlink(t, path.join(outside, "package.json"), path.join(tmp, "package.json"))) return;
      if (!trySymlink(t, path.join(outside, "Makefile"), path.join(tmp, "Makefile"))) return;
      const { report } = auditJson(tmp, ["--fail-on", "never"]);
      assert.deepEqual(ids(report), ["no-package-json@AGENTS.md:1", "no-makefile@AGENTS.md:2"]);
    });
  } finally {
    fs.rmSync(outside, { recursive: true, force: true });
  }
});

test("FIFOs named like inputs are skipped instead of blocking the audit", (t) => {
  if (process.platform === "win32") {
    t.skip("mkfifo is not available on Windows");
    return;
  }
  withTempRepo({ "AGENTS.md": "- `npm run build`\n- `make all`\n" }, (tmp) => {
    for (const name of ["package.json", "Makefile", "sub/AGENTS.md"]) {
      fs.mkdirSync(path.dirname(path.join(tmp, name)), { recursive: true });
      const made = spawnSync("mkfifo", [path.join(tmp, name)]);
      assert.equal(made.status, 0, "mkfifo failed for " + name);
    }
    const { report, status } = auditJson(tmp, ["--fail-on", "never"], { timeout: 10_000 });
    assert.equal(status, 0, "audit did not finish");
    assert.deepEqual(report.files.map((file) => file.path), ["AGENTS.md"]);
    assert.deepEqual(ids(report), ["no-package-json@AGENTS.md:1", "no-makefile@AGENTS.md:2"]);
  });
});

test("cd targets outside the root are not followed", () => {
  withTempRepo(
    {
      "package.json": "{\"scripts\":{}}",
      "repo/AGENTS.md": "- `cd ../ && npm run outside-only`\n- `cd .. && make outside-only`\n",
      Makefile: "other:\n\techo\n",
    },
    (tmp) => {
      const { report } = auditJson(path.join(tmp, "repo"), ["--fail-on", "never"]);
      assert.deepEqual(ids(report), []);
    },
  );
});

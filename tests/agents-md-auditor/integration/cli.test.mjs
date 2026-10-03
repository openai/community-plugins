import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { auditJson, fixtures, ids, runCli, withTempRepo } from "../support/helpers.mjs";

test("--fail-on controls the exit code", () => {
  const root = path.join(fixtures, "stale");
  assert.equal(runCli(["audit", "--root", root, "--fail-on", "never"]).status, 0);
  withTempRepo({ "AGENTS.md": "# Notes\n\nPlease follow best practices.\n" }, (tmp) => {
    assert.equal(runCli(["audit", "--root", tmp]).status, 0, "info alone passes the default threshold");
    assert.equal(runCli(["audit", "--root", tmp, "--fail-on", "warning"]).status, 0);
    assert.equal(runCli(["audit", "--root", tmp, "--fail-on", "info"]).status, 1);
  });
});

test("usage and input errors exit 2 without a report", () => {
  for (const args of [
    [],
    ["bogus"],
    ["audit", "--format", "xml"],
    ["audit", "--max-bytes", "-1"],
    ["audit", "--fail-on", "sometimes"],
    ["audit", "--root"],
    ["audit", "--unknown"],
  ]) {
    const result = runCli(args);
    assert.equal(result.status, 2, args.join(" "));
  }
  const missing = runCli(["audit", "--root", path.join(fixtures, "does-not-exist")]);
  assert.equal(missing.status, 2);
  assert.equal(missing.stdout, "");
  assert.match(missing.stderr, /root does not exist/);
});

test("help and rules commands succeed", () => {
  assert.equal(runCli(["--help"]).status, 0);
  const rules = runCli(["rules", "--format", "json"]);
  assert.equal(rules.status, 0);
  assert.ok(JSON.parse(rules.stdout)["unknown-script"]);
});

test("an empty repository is not an error", () => {
  withTempRepo({ "README.md": "# nothing here\n" }, (tmp) => {
    const { status, report } = auditJson(tmp);
    assert.equal(status, 0);
    assert.deepEqual(report.summary, { files: 0, error: 0, warning: 0, info: 0 });
  });
});

test("oversize file and oversize chain are reported against the byte budget", () => {
  const line = "- Keep this synthetic instruction line long enough to add bytes.\n";
  withTempRepo(
    {
      "AGENTS.md": "# Root\n" + line.repeat(10),
      "pkg/AGENTS.md": "# Pkg\n" + line.repeat(10).replaceAll("synthetic", "nested"),
      "big/AGENTS.md": "# Big\n" + line.repeat(40).replaceAll("synthetic", "large"),
    },
    (tmp) => {
      const { report } = auditJson(tmp, ["--max-bytes", "1000"]);
      const found = ids(report);
      assert.ok(found.includes("oversize-chain@pkg/AGENTS.md:1"), found.join("\n"));
      assert.ok(found.includes("oversize-file@big/AGENTS.md:1"), found.join("\n"));
      assert.ok(!found.includes("oversize-chain@big/AGENTS.md:1"), "a file already over budget is not double-reported");
      assert.ok(!found.some((id) => id.startsWith("oversize") && id.includes("@AGENTS.md")));
    },
  );
});

test("path heuristics skip non-paths and catch case mismatches", () => {
  withTempRepo(
    {
      "src/app.js": "",
      "package.json": JSON.stringify({ scripts: { test: "node --test" } }),
      "AGENTS.md": [
        "# Refs",
        "- Versions like `v1.2.3` and `2.0.0`, hosts like `api.example.com`, branches like `feat/new-ui`.",
        "- Globs like `src/**/*.js`, placeholders like `<path>`, URLs like `https://example.com/x.md`.",
        "- Bare words like `src`, bare files like `index.js`, and dotfiles like `.env` are not checked.",
        "- Real file: `src/app.js` and `./src/app.js` and `src/app.js:12`.",
        "- Wrong case: `src/App.js`.",
        "- Missing nested config: `config/tsconfig.json`; build output like `dist/app.js` is skipped.",
      ].join("\n"),
    },
    (tmp) => {
      const { report } = auditJson(tmp);
      assert.deepEqual(ids(report), ["broken-path@AGENTS.md:6", "broken-path@AGENTS.md:7"]);
      assert.match(report.findings[0].message, /src\/App\.js/);
    },
  );
});

test("cd prefixes change where scripts are resolved", () => {
  withTempRepo(
    {
      "package.json": JSON.stringify({ scripts: { root: "x" } }),
      "api/package.json": JSON.stringify({ scripts: { dev: "x" } }),
      "AGENTS.md": "- `cd api && npm run dev`\n- `cd api && npm run root`\n- `cd missing && npm run nothing`\n",
    },
    (tmp) => {
      const { report } = auditJson(tmp);
      assert.deepEqual(ids(report), ["unknown-script@AGENTS.md:2"]);
      assert.match(report.findings[0].message, /api\/package\.json/);
    },
  );
});

test("missing package.json, missing Makefile, uncertain make targets, and unparseable package.json", () => {
  withTempRepo(
    {
      "AGENTS.md": "- `npm run build`\n- `make test`\n",
      "inc/AGENTS.md": "- `make generated`\n",
      "inc/Makefile": "include rules.mk\nbuild:\n\techo\n",
      "bad/AGENTS.md": "- `npm run anything`\n",
      "bad/package.json": "{ not json",
    },
    (tmp) => {
      const { report } = auditJson(tmp);
      assert.deepEqual(ids(report), ["no-package-json@AGENTS.md:1", "no-makefile@AGENTS.md:2", "unknown-make-target@inc/AGENTS.md:1"]);
      assert.equal(report.findings[2].severity, "warning");
    },
  );
});

test("nested files without a root file, empty files, and the discovery limit", () => {
  withTempRepo(
    {
      "a/AGENTS.md": "# A\n",
      "b/AGENTS.md": "   \n",
      "c/AGENTS.md": "# C\n",
    },
    (tmp) => {
      const { report } = auditJson(tmp, ["--max-files", "2"]);
      assert.deepEqual(ids(report), ["discovery-limit@.:0", "missing-root@.:0", "empty-file@b/AGENTS.md:1"]);
      assert.equal(report.summary.files, 2);
    },
  );
});

test("generated directories are skipped during discovery", () => {
  withTempRepo(
    {
      "AGENTS.md": "# Root\n",
      "node_modules/pkg/AGENTS.md": "- `npm run missing`\n",
      "dist/AGENTS.md": "- `npm run missing`\n",
      ".git/AGENTS.md": "- `npm run missing`\n",
    },
    (tmp) => {
      const { report } = auditJson(tmp);
      assert.deepEqual(report.files.map((file) => file.path), ["AGENTS.md"]);
      assert.equal(fs.existsSync(path.join(tmp, "dist", "AGENTS.md")), true);
    },
  );
});

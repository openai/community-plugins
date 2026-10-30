import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const cli = path.join(repoRoot, "plugins", "ponder", "scripts", "ponder.mjs");

function runCli(args) {
  return spawnSync(process.execPath, [cli, ...args], { encoding: "utf8" });
}

test("full two-pass deliberation loop enforces coverage before converging", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "ponder-loop-"));
  const sessionDir = path.join(tempRoot, "session");
  const stagingDir = path.join(tempRoot, "staging");
  fs.mkdirSync(stagingDir, { recursive: true });

  try {
    const init = runCli([
      "init",
      "--session-dir",
      sessionDir,
      "--goal",
      "Audit two synthetic units",
      "--lens",
      "code",
      "--min-passes",
      "2",
    ]);
    assert.equal(init.status, 0, init.stderr);

    const unitsFile = path.join(stagingDir, "units.json");
    fs.writeFileSync(
      unitsFile,
      JSON.stringify(
        [
          {
            id: "unit.a",
            path: "src/a.js",
            kind: "module",
            label: "Module A",
          },
          {
            id: "unit.b",
            path: "src/b.js",
            kind: "module",
            label: "Module B",
          },
        ],
        null,
        2,
      ),
    );

    const enumerate = runCli([
      "enumerate",
      "--session-dir",
      sessionDir,
      "--units-file",
      unitsFile,
    ]);
    assert.equal(enumerate.status, 0, enumerate.stderr);

    const delib1 = path.join(stagingDir, "delib1.json");
    fs.writeFileSync(
      delib1,
      JSON.stringify({
        skeptic: "Checked src/a.js; unit.b is not graded yet.",
        builder: "Module A needs an explicit bounds check.",
        dreamer: "Module B still awaits grading.",
        empath: "Error messages in Module A should be clear.",
      }),
    );
    const deltas1 = path.join(stagingDir, "deltas1.jsonl");
    fs.writeFileSync(
      deltas1,
      `${JSON.stringify({
        op: "add",
        id: "f-a-1",
        unit: "unit.a",
        severity: "should",
        confidence: 0.85,
        title: "Validate bounds in Module A",
        detail: "Module A accepts negative offsets.",
        evidence: "src/a.js:4",
        recommendation: "Reject negative offsets with RangeError.",
        perspective: "builder",
        why: "Initial finding for unit.a.",
      })}\n`,
    );

    const pass1 = runCli([
      "pass",
      "--session-dir",
      sessionDir,
      "--pass",
      "1",
      "--deliberation-file",
      delib1,
      "--deltas-file",
      deltas1,
    ]);
    assert.equal(pass1.status, 0, pass1.stderr);
    const pass1Body = JSON.parse(pass1.stdout);
    assert.equal(pass1Body.canStop, false);
    assert.equal(pass1Body.verdict, "blocked");
    assert.deepEqual(pass1Body.coverage.uncoveredUnits, ["unit.b"]);

    const prematureReport = runCli(["report", "--session-dir", sessionDir]);
    assert.equal(prematureReport.status, 2);
    assert.equal(JSON.parse(prematureReport.stderr).code, "SESSION_NOT_CONVERGED");

    const delib2 = path.join(stagingDir, "delib2.json");
    fs.writeFileSync(
      delib2,
      JSON.stringify({
        skeptic: "Graded unit.b and re-verified unit.a.",
        builder: "Module B is clean; recorded note finding.",
        dreamer: "Both units covered.",
        empath: "Both modules have clear caller contracts.",
      }),
    );
    const deltas2 = path.join(stagingDir, "deltas2.jsonl");
    fs.writeFileSync(
      deltas2,
      [
        JSON.stringify({
          op: "keep",
          id: "f-a-1",
          perspective: "skeptic",
          why: "Re-verified src/a.js:4.",
        }),
        JSON.stringify({
          op: "add",
          id: "f-b-1",
          unit: "unit.b",
          severity: "note",
          confidence: 0.9,
          title: "Module B validates inputs cleanly",
          detail: "Module B rejects empty payloads.",
          evidence: "src/b.js:2",
          recommendation: "Keep existing input check.",
          perspective: "builder",
          why: "Complete coverage for unit.b.",
        }),
      ].join("\n") + "\n",
    );

    const pass2 = runCli([
      "pass",
      "--session-dir",
      sessionDir,
      "--pass",
      "2",
      "--deliberation-file",
      delib2,
      "--deltas-file",
      deltas2,
    ]);
    assert.equal(pass2.status, 0, pass2.stderr);
    const pass2Body = JSON.parse(pass2.stdout);
    assert.equal(pass2Body.coverage.complete, true);
    assert.equal(pass2Body.canStop, false);
    assert.equal(pass2Body.verdict, "continue");

    const delib3 = path.join(stagingDir, "delib3.json");
    fs.writeFileSync(
      delib3,
      JSON.stringify({
        skeptic: "Re-checked f-a-1 and f-b-1; both hold with exact line evidence.",
        builder: "No further edits required.",
        dreamer: "All units and cross-unit edges are covered.",
        empath: "Caller ergonomics verified.",
      }),
    );
    const deltas3 = path.join(stagingDir, "deltas3.jsonl");
    fs.writeFileSync(
      deltas3,
      [
        JSON.stringify({
          op: "keep",
          id: "f-a-1",
          perspective: "skeptic",
          why: "Confirmed f-a-1 on pass 3.",
        }),
        JSON.stringify({
          op: "keep",
          id: "f-b-1",
          perspective: "builder",
          why: "Confirmed f-b-1 on pass 3.",
        }),
      ].join("\n") + "\n",
    );

    const pass3 = runCli([
      "pass",
      "--session-dir",
      sessionDir,
      "--pass",
      "3",
      "--deliberation-file",
      delib3,
      "--deltas-file",
      deltas3,
    ]);
    assert.equal(pass3.status, 0, pass3.stderr);
    const pass3Body = JSON.parse(pass3.stdout);
    assert.equal(pass3Body.canStop, true);
    assert.equal(pass3Body.verdict, "converged");

    const report = runCli([
      "report",
      "--session-dir",
      sessionDir,
      "--output",
      "report.md",
    ]);
    assert.equal(report.status, 0, report.stderr);
    assert.ok(fs.existsSync(path.join(sessionDir, "report.md")));
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { pluginRoot, validatePackage } from "../../../scripts/validate-aimlapi.mjs";

const python = process.env.AIMLAPI_TEST_PYTHON || "python3";
const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
for (const key of Object.keys(env)) if (key.startsWith("AIMLAPI_")) delete env[key];

function copy(t) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "aimlapi-package-"));
  t.after(() => fs.rmSync(temporary, { recursive: true, force: true }));
  const root = path.join(temporary, "plugin");
  fs.cpSync(pluginRoot, root, { recursive: true });
  return { root, temporary };
}

function invoke(root, temporary, script, args = []) {
  const result = spawnSync(python, [path.join(root, "skills", script), ...args], {
    cwd: temporary, env, encoding: "utf8", timeout: 10_000,
  });
  assert.ifError(result.error);
  return result;
}

test("copied plugin discovers all skills and runs helper CLIs outside the checkout", (t) => {
  const { root, temporary } = copy(t);
  assert.equal(validatePackage(root).skills, 13);
  for (const script of [
    "aimlapi-models/scripts/models.py", "aimlapi-batch/scripts/batch.py",
    "aimlapi-async-jobs/scripts/job.py", "aimlapi-speech/scripts/speech.py",
    "aimlapi-decisions/scripts/decide.py",
  ]) {
    const result = invoke(root, temporary, script, ["--help"]);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /usage:/u);
  }
});

test("copied batch checker accepts valid JSONL and rejects duplicates without a key", (t) => {
  const { root, temporary } = copy(t);
  const filename = path.join(temporary, "requests.jsonl");
  const row = (custom_id) => JSON.stringify({
    custom_id, method: "POST", url: "/v1/chat/completions",
    body: { model: "fixture/model", messages: [{ role: "user", content: "synthetic" }], max_tokens: 1 },
  });
  fs.writeFileSync(filename, `${row("a")}\n${row("b")}\n`);
  const good = invoke(root, temporary, "aimlapi-batch/scripts/batch.py", ["check", filename]);
  assert.equal(good.status, 0, good.stderr);
  fs.writeFileSync(filename, `${row("a")}\n${row("a")}\n`);
  const bad = invoke(root, temporary, "aimlapi-batch/scripts/batch.py", ["check", filename]);
  assert.notEqual(bad.status, 0);
  assert.match(bad.stderr, /duplicate custom_id/u);
  assert.match(bad.stderr, /nothing was uploaded/u);
  assert.ok(!fs.existsSync(`${filename}.aimlapi-batch.json`));
});

test("authenticated helpers stop on missing credentials", (t) => {
  const { root, temporary } = copy(t);
  const body = JSON.stringify({ model: "fixture/model", text: "synthetic" });
  for (const [script, args] of [
    ["aimlapi-async-jobs/scripts/job.py", ["poll", "video", "fixture-id"]],
    ["aimlapi-batch/scripts/batch.py", ["status", "fixture-id"]],
    ["aimlapi-speech/scripts/speech.py", ["tts", "--body", body, "--out", "audio.mp3"]],
    ["aimlapi-sandbox/scripts/try.py", ["chat", body]],
  ]) {
    const result = invoke(root, temporary, script, args);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /AIMLAPI_API_KEY is not set/u);
  }
  assert.ok(!fs.existsSync(path.join(temporary, "audio.mp3")));
});

test("provenance validation rejects modified or unexpected package files", (t) => {
  const { root } = copy(t);
  const skill = path.join(root, "skills/aimlapi-chat/SKILL.md");
  fs.appendFileSync(skill, "\nchanged\n");
  assert.throws(() => validatePackage(root), /upstream content changed/u);
  fs.copyFileSync(path.join(pluginRoot, "skills/aimlapi-chat/SKILL.md"), skill);
  fs.writeFileSync(path.join(root, ".mcp.json"), "{}\n");
  assert.throws(() => validatePackage(root), /unexpected or missing package files/u);
});

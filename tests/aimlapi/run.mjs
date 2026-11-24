import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { pluginRoot, validatePackage } from "../../scripts/validate-aimlapi.mjs";

const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
const testRoot = fileURLToPath(new URL("./", import.meta.url));
const python = process.env.AIMLAPI_TEST_PYTHON || "python3";
const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
for (const key of Object.keys(env)) {
  if (key.startsWith("AIMLAPI_")) delete env[key];
}
env.AIMLAPI_TEST_PYTHON = python;

console.log(validatePackage(pluginRoot));
const receipt = JSON.parse(fs.readFileSync(path.join(testRoot, "support/UPSTREAM.json"), "utf8"));
for (const [relative, record] of Object.entries(receipt.files)) {
  const digest = createHash("sha256").update(fs.readFileSync(path.join(testRoot, relative))).digest("hex");
  assert.equal(digest, record.sha256, `adapted upstream test changed: ${relative}`);
}

function run(command, args) {
  const result = spawnSync(command, args, { cwd: repoRoot, env, stdio: "inherit", timeout: 180_000 });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

run(python, ["-m", "unittest", "discover", "-s", "tests/aimlapi/unit", "-p", "test_*.py", "-v"]);
run(python, ["-m", "unittest", "discover", "-s", "tests/aimlapi/security", "-p", "test_*.py", "-v"]);
run(process.execPath, [
  "--test", "--test-concurrency=1",
  "tests/aimlapi/integration/packaged.test.mjs",
  "tests/aimlapi/security/verify-webhook.test.mjs",
]);
console.log("AI/ML API master suite passed; no live provider tests were run.");

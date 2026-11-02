import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fixture, run } from "../support/fixture.mjs";

test("session-only rejects managed storage inside the project without writes", (t) => {
  const item = fixture(t);
  fs.writeFileSync(path.join(item.project, "package.json"), "{}\n");
  const unsafe = path.join(item.project, ".managed");
  const result = run(item, ["session", "--session", "session-only", "--json"], { env: { CLEAN_DEVELOPMENT_ROOT: unsafe } });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Managed storage must be outside the project/);
  assert.equal(fs.existsSync(unsafe), false);
  assert.equal(fs.existsSync(path.join(item.project, ".clean-development.json")), false);
});

test("prune never adopts an unregistered direct child of the managed build root", (t) => {
  const item = fixture(t);
  const unowned = path.join(item.managed, "builds", "unowned-output");
  fs.mkdirSync(unowned, { recursive: true });
  fs.writeFileSync(path.join(unowned, "keep.txt"), "user-owned\n");
  const result = run(item, ["prune", "--older-than", "0", "--apply", "--json"]);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout).entries, []);
  assert.equal(fs.readFileSync(path.join(unowned, "keep.txt"), "utf8"), "user-owned\n");
});

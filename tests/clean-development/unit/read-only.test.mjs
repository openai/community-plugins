import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fixture, pathListing, run } from "../support/fixture.mjs";

test("cold status and doctor are read-only", (t) => {
  const item = fixture(t);
  fs.writeFileSync(path.join(item.project, "package.json"), "{}\n");
  const before = pathListing(item.root);
  const status = run(item, ["status", "--json"]);
  assert.equal(status.status, 0, status.stderr);
  assert.equal(JSON.parse(status.stdout).configured, false);
  const doctor = run(item, ["doctor", "--json"]);
  assert.equal(doctor.status, 1);
  assert.equal(JSON.parse(doctor.stdout).ok, false);
  assert.deepEqual(pathListing(item.root), before);
  assert.equal(fs.existsSync(path.join(item.project, ".clean-development.json")), false);
});

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fixture, pathListing, run } from "../support/fixture.mjs";

test("session preview is read-only and reports a project proposal", (t) => {
  const item = fixture(t);
  fs.writeFileSync(path.join(item.project, "package.json"), "{}\n");
  const before = pathListing(item.root);
  const result = run(item, ["session", "--dry-run", "--json"]);
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.dryRun, true);
  assert.equal(output.plan.projectConfig.status, "proposed");
  assert.equal(fs.existsSync(item.managed), false);
  assert.equal(fs.existsSync(path.join(item.project, ".clean-development.json")), false);
  assert.deepEqual(pathListing(item.root), before);
});

test("session-only routes npm to managed cache without project configuration", (t) => {
  const item = fixture(t);
  fs.writeFileSync(path.join(item.project, "package.json"), "{}\n");
  const capture = path.join(item.root, "npm-cache");
  const fakeNpm = path.join(item.fakeBin, "npm");
  fs.writeFileSync(fakeNpm, `#!${process.execPath}\nrequire('node:fs').writeFileSync(process.env.CAPTURE, process.env.npm_config_cache || '');\n`, { mode: 0o755 });
  fs.chmodSync(fakeNpm, 0o755);
  const result = run(item, ["run", "--session", "session-only", "--", "npm", "--version"], { env: { CAPTURE: capture } });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(
    fs.readFileSync(capture, "utf8"),
    path.join(fs.realpathSync(item.root), "managed", "caches", "node", "npm")
  );
  assert.equal(fs.existsSync(path.join(item.project, ".clean-development.json")), false);
});

test("an explicit npm cache override is preserved", (t) => {
  const item = fixture(t);
  fs.writeFileSync(path.join(item.project, "package.json"), "{}\n");
  const capture = path.join(item.root, "npm-cache");
  const fakeNpm = path.join(item.fakeBin, "npm");
  fs.writeFileSync(fakeNpm, `#!${process.execPath}\nrequire('node:fs').writeFileSync(process.env.CAPTURE, process.env.npm_config_cache || '');\n`, { mode: 0o755 });
  fs.chmodSync(fakeNpm, 0o755);
  const userCache = path.join(item.root, "user-cache");
  const result = run(item, ["run", "--session", "session-only", "--", "npm", "--version"], { env: { CAPTURE: capture, npm_config_cache: userCache } });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.readFileSync(capture, "utf8"), userCache);
});

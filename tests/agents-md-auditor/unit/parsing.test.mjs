import assert from "node:assert/strict";
import test from "node:test";
import {
  parseMakeInvocation,
  parseMakeTargets,
  parsePackageScript,
  scanMarkdown,
  splitCommandSegments,
} from "../../../plugins/agents-md-auditor/scripts/agents-md-audit.mjs";

test("package script parsing covers run forms and npm lifecycle shortcuts", () => {
  assert.deepEqual(parsePackageScript("npm run build"), { manager: "npm", script: "build", verb: "run", workspace: false });
  assert.equal(parsePackageScript("pnpm run test:e2e -- --watch").script, "test:e2e");
  assert.equal(parsePackageScript("yarn run lint").script, "lint");
  assert.equal(parsePackageScript("bun run dev").script, "dev");
  assert.equal(parsePackageScript("npm test").script, "test");
  assert.equal(parsePackageScript("npm t").script, "test");
  assert.equal(parsePackageScript("npm start").script, "start");
  assert.equal(parsePackageScript("npm install").script, null);
  assert.equal(parsePackageScript("npm run").script, null);
  assert.equal(parsePackageScript("npm run test:<plugin-name>").script, null, "placeholders are skipped");
  assert.equal(parsePackageScript("npm run ${SCRIPT}").script, null);
  assert.equal(parsePackageScript("yarn build").script, null, "bare yarn commands are ambiguous and skipped");
  assert.equal(parsePackageScript("echo hello"), null);
});

test("workspace-targeted commands are skipped instead of guessed", () => {
  for (const command of [
    "pnpm --filter api run dev",
    "pnpm -F api run dev",
    "npm run build -w packages/web",
    "npm run build --workspace=web",
    "npm --prefix api run dev",
    "yarn workspace web run build",
    "pnpm -r run test",
  ]) {
    const parsed = parsePackageScript(command);
    assert.equal(parsed.workspace, true, command);
  }
});

test("command lines split on shell operators", () => {
  assert.deepEqual(splitCommandSegments("cd api && npm run dev; make test | tee log"), ["cd api", "npm run dev", "make test", "tee log"]);
});

test("make invocation parsing ignores flags and variables and skips redirection", () => {
  assert.deepEqual(parseMakeInvocation("make -j4 build test VERBOSE=1"), { targets: ["build", "test"], redirected: false });
  assert.deepEqual(parseMakeInvocation("make"), { targets: [], redirected: false });
  assert.equal(parseMakeInvocation("make -C sub build").redirected, true);
  assert.equal(parseMakeInvocation("make -f other.mk build").redirected, true);
  assert.equal(parseMakeInvocation("cmake --build ."), null);
});

test("Makefile parsing finds targets, phony names, includes, and pattern rules", () => {
  const parsed = parseMakeTargets([
    "VAR := value",
    "OTHER = x",
    "build test: deps",
    "\tbuild-command: not-a-target",
    ".PHONY: lint fmt",
    "%.o: %.c",
    "$(OUT): build",
    "-include extra.mk",
    "deploy:: build # comment",
  ].join("\n"));
  for (const name of ["build", "test", "lint", "fmt", "deploy"]) assert.ok(parsed.targets.has(name), name);
  for (const name of ["VAR", "OTHER", "build-command", ".PHONY"]) assert.ok(!parsed.targets.has(name), name);
  assert.equal(parsed.hasInclude, true);
  assert.equal(parsed.hasPatternRule, true);
});

test("markdown scanning separates prose, inline code, links, shell fences, and other fences", () => {
  const scan = scanMarkdown([
    "# Title",
    "Run `npm test` and see [docs](docs/a.md#part) or `src/x.js`.",
    "```sh",
    "$ make build",
    "# a comment",
    "```",
    "```json",
    "{ \"script\": \"npm run nope\" }",
    "```",
    "~~~",
    "pnpm run fenced",
    "~~~",
  ].join("\n"));
  assert.deepEqual(scan.headings.map((h) => h.text), ["Title"]);
  assert.deepEqual(scan.inlineCode.map((c) => c.text), ["npm test", "src/x.js"]);
  assert.deepEqual(scan.links.map((l) => l.target), ["docs/a.md#part"]);
  assert.deepEqual(scan.commandLines.map((c) => c.text), ["make build", "pnpm run fenced"]);
});

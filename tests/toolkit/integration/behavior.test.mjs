import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const python = process.env.PYTHON ?? "python3";
const supported = process.platform !== "win32";

function withPackage(runTest) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "toolkit-behavior-"));
  try {
    const plugin = path.join(root, "toolkit");
    const repository = path.join(root, "repository");
    const codexHome = path.join(root, "codex-home");
    fs.cpSync(path.join(repoRoot, "plugins", "toolkit"), plugin, { recursive: true });
    fs.mkdirSync(path.join(repository, ".git"), { recursive: true });
    fs.mkdirSync(path.join(repository, ".codex"), { recursive: true });
    fs.writeFileSync(path.join(repository, ".codex", "config.toml"), "");
    fs.mkdirSync(codexHome);
    const scripts = path.join(plugin, "skills", "perform", "scripts");
    const bundled = JSON.parse(fs.readFileSync(path.join(plugin, "skills", "perform", "assets", "toolkit_perform_actions", "actions.json"), "utf8"));
    const invoke = (script, args = []) => {
      const result = spawnSync(python, [path.join(scripts, script), ...args, `--cwd=${repository}`], {
        cwd: repository,
        encoding: "utf8",
        env: { ...process.env, CODEX_HOME: codexHome, PYTHONDONTWRITEBYTECODE: "1" },
      });
      assert.equal(result.error, undefined, String(result.error));
      return { status: result.status, payload: JSON.parse(result.stdout), stderr: result.stderr };
    };
    const writeActions = (directory, filename, actions) => {
      fs.mkdirSync(directory, { recursive: true });
      fs.writeFileSync(path.join(directory, filename), JSON.stringify({ version: 1, actions }));
    };
    runTest({ root, plugin, repository, codexHome, bundled, invoke, writeActions });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test("inspect and render report the winning portable source and preserve strict selection", { skip: !supported }, () => {
  withPackage(({ repository, codexHome, bundled, invoke, writeActions }) => {
    const bundledResult = invoke("get_perform_action.py", ["--inspect=find-todos[agnostic]"]);
    assert.equal(bundledResult.status, 0, bundledResult.stderr);
    assert.deepEqual(bundledResult.payload.definition_source, { kind: "bundled", path: "<bundled-actions>/actions.json", source_order: 0 });

    const base = bundled.actions["find-todos"].agnostic;
    writeActions(path.join(codexHome, "toolkit_perform_actions"), "20-user.json", { "find-todos": { agnostic: { ...base, prompt: "User override." } } });
    writeActions(path.join(repository, ".codex", "toolkit_perform_actions"), "90-repository.json", { "find-todos": { agnostic: { ...base, prompt: "Repository override." } } });

    const inspected = invoke("get_perform_action.py", ["--inspect=find-todos[agnostic]"]);
    const rendered = invoke("get_perform_action.py", ["--render=find-todos[agnostic]"]);
    assert.equal(inspected.status, 0, inspected.stderr);
    assert.equal(rendered.status, 0, rendered.stderr);
    assert.equal(inspected.payload.prompt, "No edits. Repository override.");
    assert.equal(rendered.payload.prompt, "No edits. Repository override.");
    assert.deepEqual(rendered.payload.definition_source, inspected.payload.definition_source);
    assert.equal(inspected.payload.definition_source.kind, "repository");
    assert.equal(inspected.payload.definition_source.path, "./.codex/toolkit_perform_actions/90-repository.json");
    assert.ok(inspected.payload.definition_source.source_order > bundledResult.payload.definition_source.source_order);

    const missingStrict = invoke("get_perform_action.py", ["--inspect=find-todos[python]"]);
    assert.equal(missingStrict.status, 2);
    assert.equal(missingStrict.payload.error.code, "not_found");
  });
});

test("a malformed action file blocks configured actions while built-in help remains available", { skip: !supported }, () => {
  withPackage(({ repository, invoke }) => {
    const actions = path.join(repository, ".codex", "toolkit_perform_actions");
    fs.mkdirSync(actions);
    fs.writeFileSync(path.join(actions, "90-broken.json"), "{");

    const listed = invoke("list_perform_actions.py");
    const inspected = invoke("get_perform_action.py", ["--inspect=find-todos[agnostic]"]);
    const help = invoke("get_perform_action.py", ["--inspect=help[agnostic]"]);
    const output = path.join(repository, "catalog.md");
    const write = invoke("write_perform_action_catalogue.py", [`--output=${output}`]);
    assert.equal(listed.status, 3);
    assert.equal(listed.payload.error.code, "fatal_catalog");
    assert.ok(listed.payload.diagnostics.some((value) => value.includes("90-broken.json")));
    assert.equal(inspected.status, 3);
    assert.equal(inspected.payload.error.code, "fatal_catalog");
    assert.equal(help.status, 0, help.stderr);
    assert.equal(help.payload.definition_source, null);
    assert.equal(write.status, 3);
    assert.equal(fs.existsSync(output), false);
  });
});

test("invalid variants block their action without disabling unrelated or exact sibling selectors", { skip: !supported }, () => {
  withPackage(({ repository, bundled, invoke, writeActions }) => {
    const invalidFind = { ...bundled.actions["find-todos"].agnostic, prompt: 42 };
    const invalidAudit = { ...bundled.actions["audit-project-setup"].agnostic, prompt: 42 };
    writeActions(path.join(repository, ".codex", "toolkit_perform_actions"), "90-invalid.json", {
      "find-todos": { agnostic: invalidFind },
      "audit-project-setup": { agnostic: invalidAudit },
    });

    const listed = invoke("list_perform_actions.py");
    assert.equal(listed.status, 0, listed.stderr);
    assert.ok(listed.payload.blocked_actions.includes("find-todos"));
    assert.ok(listed.payload.blocked_actions.includes("audit-project-setup"));
    assert.ok(listed.payload.variants.some((value) => value.selector === "audit-project-setup[python]"));
    assert.ok(!listed.payload.variants.some((value) => value.selector === "find-todos[agnostic]"));

    const named = invoke("list_perform_actions.py", ["--name=find-todos", "--fallback"]);
    const strict = invoke("get_perform_action.py", ["--inspect=find-todos[agnostic]"]);
    assert.equal(named.status, 2);
    assert.equal(named.payload.error.code, "invalid_override");
    assert.equal(strict.status, 2);
    assert.equal(strict.payload.error.code, "invalid_override");

    const sibling = invoke("get_perform_action.py", ["--inspect=audit-project-setup[python]"]);
    const unrelated = invoke("get_perform_action.py", ["--inspect=check-cross-platform[agnostic]"]);
    assert.equal(sibling.status, 0, sibling.stderr);
    assert.equal(unrelated.status, 0, unrelated.stderr);

    const output = path.join(repository, "catalog.md");
    const write = invoke("write_perform_action_catalogue.py", [`--output=${output}`]);
    assert.equal(write.status, 2);
    assert.equal(write.payload.error.code, "invalid_override");
    assert.equal(fs.existsSync(output), false);
  });
});

test("catalogue output is stable and refuses unmarked files and symbolic-link targets", { skip: !supported }, () => {
  withPackage(({ repository, invoke }) => {
    const output = path.join(repository, "catalog.md");
    fs.writeFileSync(output, "user-authored content\n");
    const unmarked = invoke("write_perform_action_catalogue.py", [`--output=${output}`]);
    assert.equal(unmarked.status, 2);
    assert.equal(unmarked.payload.error.code, "unsafe_output");
    assert.equal(fs.readFileSync(output, "utf8"), "user-authored content\n");

    const linked = path.join(repository, "linked-catalog.md");
    fs.symlinkSync(output, linked);
    const symlink = invoke("write_perform_action_catalogue.py", [`--output=${linked}`]);
    assert.equal(symlink.status, 2);
    assert.equal(symlink.payload.error.code, "unsafe_output");
    assert.equal(fs.readFileSync(output, "utf8"), "user-authored content\n");

    fs.writeFileSync(output, "");
    const first = invoke("write_perform_action_catalogue.py", [`--output=${output}`]);
    const content = fs.readFileSync(output, "utf8");
    const second = invoke("write_perform_action_catalogue.py", [`--output=${output}`]);
    assert.equal(first.status, 0, first.stderr);
    assert.equal(first.payload.changed, true);
    assert.ok(content.startsWith("<!-- toolkit-perform-action-catalogue:v1 -->\n"));
    assert.equal(second.status, 0, second.stderr);
    assert.equal(second.payload.changed, false);
    assert.equal(fs.readFileSync(output, "utf8"), content);
  });
});

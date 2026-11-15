import assert from "node:assert/strict";
import { cp, lstat, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { execFile } from "node:child_process";
import { tmpdir } from "node:os";
import { promisify } from "node:util";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { McpProcess } from "./support/mcp-process.mjs";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const execFileAsync = promisify(execFile);
async function filesAt(directory, prefix = "") {
  const result = [];
  for (const entry of await readdir(path.join(directory, prefix), { withFileTypes: true })) {
    const relative = path.join(prefix, entry.name);
    assert.equal(entry.isSymbolicLink(), false, `package must be self-contained: ${relative}`);
    if (entry.isDirectory()) result.push(...await filesAt(directory, relative));
    else result.push(relative);
  }
  return result.sort();
}
async function validateReferences(directory) {
  assert.ok((await lstat(path.join(directory, "SKILL.md"))).isFile());
  for (const file of await filesAt(directory)) {
    if (!file.endsWith(".md")) continue;
    const contents = await readFile(path.join(directory, file), "utf8");
    const links = [
      ...[...contents.matchAll(/\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)/gu)].map((match) => match[1]),
      ...[...contents.matchAll(/^\s*\[[^\]]+\]:\s*(\S+)/gmu)].map((match) => match[1])
    ];
    for (const raw of links) {
      const href = raw.replace(/^<|>$/gu, "");
      if (/^https?:\/\//iu.test(href) || href.startsWith("#")) continue;
      const target = path.resolve(directory, path.dirname(file), decodeURIComponent(href.split("#")[0]));
      const relative = path.relative(directory, target);
      assert.ok(relative && relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative), `reference escapes package: ${href}`);
      assert.ok((await lstat(target)).isFile(), `missing packaged reference: ${href}`);
    }
  }
}
test("archived Skill works in isolated project and global installation layouts", async (t) => {
  const temporary = await mkdtemp(path.join(tmpdir(), "electron skill package "));
  t.after(() => rm(temporary, { recursive: true, force: true }));
  const archive = path.join(temporary, "skill.tar");
  const source = path.join(root, "plugins", "electron-agent-debug", "skills", "electron-debug");
  await execFileAsync("tar", ["-cf", archive, "-C", path.dirname(source), "electron-debug"]);
  const sourceFiles = await filesAt(source);
  assert.ok(sourceFiles.includes(path.join("references", "mcp-setup.md")));
  for (const layout of ["project/.agents/skills", "isolated-global/skills"]) {
    const destination = path.join(temporary, layout);
    await mkdir(destination, { recursive: true });
    await execFileAsync("tar", ["-xf", archive, "-C", destination]);
    const installed = path.join(destination, "electron-debug");
    assert.deepEqual(await filesAt(installed), sourceFiles);
    for (const file of sourceFiles) {
      assert.deepEqual(await readFile(path.join(installed, file)), await readFile(path.join(source, file)));
    }
    await validateReferences(installed);
    assert.ok(sourceFiles.includes("VERSION.md"));
    let runtimeRoot = installed;
    if (layout.startsWith("isolated-global")) {
      runtimeRoot = path.join(temporary, "global installation alias");
      await symlink(installed, runtimeRoot, process.platform === "win32" ? "junction" : "dir");
    }
    const audit = path.join(temporary, `audit-${layout.startsWith("project") ? "project" : "global"}`);
    const client = new McpProcess({
      entry: path.join(runtimeRoot, "runtime/launch.cjs"),
      cwd: runtimeRoot,
      nodeArgs: ["--require", path.join(root, "tests/electron-agent-debug/support/isolated-runtime-guard.cjs")],
      env: {
        NODE_PATH: "",
        NODE_OPTIONS: "",
        ISOLATED_SKILL_ROOT: runtimeRoot,
        ISOLATED_AUDIT_FILE: audit
      }
    });
    try {
      await client.request(1, "initialize", {
        protocolVersion: "2025-03-26",
        capabilities: {},
        clientInfo: { name: "isolated-installed-skill", version: "1.0.0" }
      });
      client.notify("notifications/initialized", {});
      const tools = await client.request(2, "tools/list", {});
      assert.deepEqual(
        tools.result?.tools?.map((tool) => tool.name),
        ["electron_attach", "electron_status", "electron_targets", "electron_logs"]
      );
      for (const [id, name] of [[3, "electron_attach"], [4, "electron_targets"]]) {
        const response = await client.request(id, "tools/call", { name, arguments: {} });
        assert.equal(response.result?.structuredContent?.ok, false);
        assert.equal(response.result?.structuredContent?.error?.code, "ENDPOINT_NOT_CONFIGURED");
      }
      const status = await client.request(5, "tools/call", { name: "electron_status", arguments: {} });
      assert.equal(status.result?.structuredContent?.data?.main?.status, "unconfigured");
      assert.equal(status.result?.structuredContent?.data?.renderer?.status, "unconfigured");
      const logs = await client.request(6, "tools/call", { name: "electron_logs", arguments: {} });
      assert.deepEqual(logs.result?.structuredContent?.data?.events, []);
      client.child.stdin.end();
      await client.waitForExit();
      assert.equal(client.child.exitCode, 0);
      await assert.rejects(
        readFile(audit),
        /ENOENT/u,
        "offline package must not attempt network or load any external dependency"
      );
    } finally {
      await client.close();
    }
    await rm(path.join(installed, "references", "mcp-setup.md"));
    await assert.rejects(
      validateReferences(installed),
      /ENOENT/u,
      "must detect a package that omitted its referenced setup guide"
    );
    await writeFile(path.join(installed, "SKILL.md"), "[outside](../../outside.md)", "utf8");
    await assert.rejects(validateReferences(installed), /reference escapes package/u);
  }
});
test("runtime integrity errors stop before executing the bundled server", { timeout: 15e3 }, async (t) => {
  const temporary = await mkdtemp(path.join(tmpdir(), "electron runtime integrity "));
  t.after(() => rm(temporary, { recursive: true, force: true }));
  const installed = path.join(temporary, "skill");
  await cp(path.join(root, "plugins/electron-agent-debug/skills/electron-debug"), installed, { recursive: true });
  const launcher = path.join(installed, "runtime/launch.cjs");
  const result = await execFileAsync(process.execPath, [launcher, "--verify"], {
    cwd: temporary,
    timeout: 3e3,
    env: { ...process.env, NODE_OPTIONS: "", NODE_PATH: "" }
  });
  const verified = JSON.parse(result.stdout);
  assert.equal(verified.ok, true);
  const manifestFile = path.join(installed, "runtime/manifest.json");
  const manifestBytes = await readFile(manifestFile);
  const manifest = JSON.parse(manifestBytes.toString());
  assert.equal(verified.skillVersion, manifest.skillVersion);
  assert.equal(verified.mcpVersion, manifest.mcpVersion);
  for (const filename of ["server.cjs", "launch.cjs", "THIRD_PARTY_LICENSES.txt"]) {
    const file = path.join(installed, "runtime", filename);
    const original = await readFile(file);
    await writeFile(file, Buffer.concat([original, Buffer.from("\n// tampered\n")]));
    await assert.rejects(
      execFileAsync(process.execPath, [launcher], { cwd: temporary, timeout: 2e3 }),
      (error) => {
        const failure = error;
        assert.equal(failure.code, 1);
        assert.equal(failure.killed, false);
        assert.equal(failure.stdout, "");
        assert.match(failure.stderr ?? "", /PACKAGE_INTEGRITY_ERROR/u);
        return true;
      }
    );
    await writeFile(file, original);
  }
  for (const broken of [
    "{",
    JSON.stringify({ ...manifest, files: { "../outside.cjs": "0".repeat(64) } }),
    JSON.stringify({ ...manifest, entry: "../outside.cjs" })
  ]) {
    await writeFile(manifestFile, broken);
    await assert.rejects(
      execFileAsync(process.execPath, [launcher], { timeout: 2e3 }),
      (error) => error.code === 1 && /PACKAGE_INTEGRITY_ERROR/u.test(error.stderr ?? "")
    );
  }
  await writeFile(manifestFile, manifestBytes);
  await rm(path.join(installed, "runtime/server.cjs"));
  await assert.rejects(
    execFileAsync(process.execPath, [launcher], { timeout: 2e3 }),
    (error) => error.code === 1 && /PACKAGE_INTEGRITY_ERROR/u.test(error.stderr ?? "")
  );
});

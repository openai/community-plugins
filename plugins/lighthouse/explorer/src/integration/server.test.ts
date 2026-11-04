import assert from "node:assert/strict";
import { mkdtemp, mkdir, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Script } from "node:vm";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

async function benchmarkTask(directory: string, name: string, task: string) {
  await mkdir(join(directory, "tasks", task), { recursive: true });
  await mkdir(join(directory, "review", task), { recursive: true });
  await writeFile(join(directory, "tasks", task, "task.toml"), `[task]\nname = "${name}/${task}"\n`);
  await writeFile(join(directory, "tasks", task, "instruction.md"), `Do ${task}.`);
  await writeFile(join(directory, "review", task, "requirements.md"), `Private rubric for ${task}.`);
}

async function benchmark(parent: string, name: string, task: string) {
  const directory = join(parent, name);
  await benchmarkTask(directory, name, task);
  await writeFile(join(directory, "benchmark.lighthouse"), JSON.stringify({ schemaVersion: 1, name }));
  await writeFile(join(directory, ".env"), "SECRET=never-visible");
  return join(directory, "benchmark.lighthouse");
}

function metadata(path: string) {
  return { "openai/resource": { path } };
}

test("the installed-shape stdio server binds each tool to host metadata and serves the bundled UI", async () => {
  const temporary = await mkdtemp(join(tmpdir(), "lighthouse-mcp-"));
  const client = new Client({ name: "lighthouse-integration-test", version: "1" });
  const transport = new StdioClientTransport({ command: process.execPath, args: [join(root, "dist/server.cjs")], env: { LIGHTHOUSE_BENCHMARK_HOME: temporary }, stderr: "pipe" });
  try {
    const first = await benchmark(temporary, "First", "one");
    const second = await benchmark(temporary, "Second", "two");
    await client.connect(transport);
    const listed = await client.listTools();
    const opener = listed.tools.find((entry) => entry.name === "lighthouse_open_benchmark");
    const detail = listed.tools.find((entry) => entry.name === "lighthouse_explorer_snapshot");
    assert.deepEqual(opener?._meta?.["openai/ui"], { entrypoints: [{ type: "file", extensions: [".lighthouse"] }] });
    assert.deepEqual(detail?._meta?.["ui"], { visibility: ["app"] });

    const content = await client.readResource({ uri: "ui://lighthouse/explorer" });
    assert.equal(content.contents[0]?.mimeType, "text/html;profile=mcp-app");
    const page = content.contents[0];
    assert.ok(page && "text" in page && typeof page.text === "string" && page.text.includes("Lighthouse Explorer"));
    const embeddedScript = /<script>([\s\S]*?)<\/script>/.exec(page.text)?.[1];
    assert.ok(embeddedScript);
    assert.doesNotThrow(() => new Script(embeddedScript));

    const file = { name: "benchmark.lighthouse", resourceUri: "host-resource://marker" };
    const initial = await client.callTool({ name: "lighthouse_open_benchmark", arguments: { file }, _meta: metadata(first) });
    assert.deepEqual(initial.structuredContent, { name: "First", taskCount: 1 });
    assert.doesNotMatch(JSON.stringify(initial), /Private rubric/);

    const firstData = await client.callTool({ name: "lighthouse_explorer_snapshot", arguments: { file }, _meta: metadata(first) });
    assert.match(JSON.stringify(firstData.structuredContent), /Private rubric for one/);
    assert.doesNotMatch(JSON.stringify(firstData.structuredContent), /Private rubric for two|never-visible/);
    const secondData = await client.callTool({ name: "lighthouse_explorer_snapshot", arguments: { file }, _meta: metadata(second) });
    assert.match(JSON.stringify(secondData.structuredContent), /Private rubric for two/);
    assert.doesNotMatch(JSON.stringify(secondData.structuredContent), /Private rubric for one/);

    const unbound = await client.callTool({ name: "lighthouse_explorer_snapshot", arguments: { file: { ...file, name: first, resourceUri: first } } });
    assert.equal(unbound.isError, true);
    const forbidden = await client.callTool({ name: "lighthouse_explorer_open_evidence", arguments: { path: ".env" }, _meta: metadata(first) });
    assert.equal(forbidden.isError, true);
    assert.doesNotMatch(JSON.stringify(forbidden), /never-visible/);
    const allowed = await client.callTool({ name: "lighthouse_explorer_open_evidence", arguments: { path: "review/one/requirements.md" }, _meta: metadata(first) });
    assert.deepEqual(allowed.structuredContent, { path: await realpath(join(dirname(first), "review/one/requirements.md")) });
  } finally {
    await client.close();
    await rm(temporary, { recursive: true, force: true });
  }
});

test("native global and thread tools open the library and only registered selections can read app-only evidence", async () => {
  const temporary = await mkdtemp(join(tmpdir(), "lighthouse-native-mcp-"));
  const client = new Client({ name: "lighthouse-native-integration-test", version: "1" });
  try {
    const home = join(temporary, "home");
    const first = await benchmark(home, "First", "one");
    const second = await benchmark(home, "Second", "two");
    const third = await benchmark(temporary, "Elsewhere", "three");
    const transport = new StdioClientTransport({
      command: process.execPath,
      args: [join(root, "dist/server.cjs")],
      env: { LIGHTHOUSE_BENCHMARK_HOME: home, LIGHTHOUSE_EXPLORER_TITLE: "Lighthouse Explorer Preview" },
      stderr: "pipe",
    });
    await client.connect(transport);
    const list = await client.listTools();
    const global = list.tools.find((tool) => tool.name === "lighthouse_explorer_home");
    const thread = list.tools.find((tool) => tool.name === "lighthouse_explorer_thread");
    assert.ok(global);
    assert.ok(thread);
    assert.equal(global.title, "Lighthouse Explorer Preview");
    assert.equal(thread.title, "Lighthouse Tasks & Rubrics");
    assert.deepEqual(global.inputSchema.properties, {});
    assert.deepEqual(global._meta?.["openai/ui"], { entrypoints: [{ type: "global" }] });
    assert.deepEqual(thread._meta?.["openai/ui"], { entrypoints: [{ type: "thread" }] });
    assert.deepEqual(global._meta?.ui, { resourceUri: "ui://lighthouse/explorer-home", visibility: ["app"] });
    assert.deepEqual(thread._meta?.ui, { resourceUri: "ui://lighthouse/explorer-thread", visibility: ["app"] });
    assert.equal(client.getServerVersion()?.icons?.length, 2);
    const resourceContents: string[] = [];
    for (const [uri, surface, preferredDisplayMode, availableDisplayModes] of [
      ["ui://lighthouse/explorer-home", "home", "inline", ["inline"]],
      ["ui://lighthouse/explorer-thread", "thread", "fullscreen", ["fullscreen"]],
      ["ui://lighthouse/explorer", "file", "fullscreen", ["inline", "fullscreen"]],
    ] as const) {
      const resource = await client.readResource({ uri });
      const entry = resource.contents[0];
      assert.ok(entry && "text" in entry && typeof entry.text === "string");
      assert.deepEqual(entry._meta?.["openai/ui"], { preferredDisplayMode, availableDisplayModes });
      assert.ok(entry.text.includes(`<html data-lighthouse-surface="${surface}"`));
      resourceContents.push(entry.text);
    }
    assert.equal(new Set(resourceContents).size, 3);
    assert.equal(new Set(resourceContents.map((html) => html.replace(/data-lighthouse-surface="\w+"/, ""))).size, 1);
    for (const name of ["lighthouse_explorer_library", "lighthouse_explorer_attach", "lighthouse_explorer_choose", "lighthouse_explorer_snapshot", "lighthouse_explorer_open_evidence", "lighthouse_explorer_authoring_context", "lighthouse_explorer_authoring_snapshot"]) {
      assert.deepEqual(list.tools.find((tool) => tool.name === name)?._meta?.ui, { visibility: ["app"] });
    }
    for (const name of [global.name, thread.name]) {
      const result = await client.callTool({ name, arguments: {} });
      assert.deepEqual(result.structuredContent, { page: "library" });
      assert.doesNotMatch(JSON.stringify(result), /Private rubric|Elsewhere|never-visible/);
    }

    const listing = await client.callTool({ name: "lighthouse_explorer_library", arguments: {} });
    const libraryPayload: unknown = listing.structuredContent;
    assert.ok(typeof libraryPayload === "object" && libraryPayload !== null && "benchmarks" in libraryPayload);
    const entries = libraryPayload.benchmarks;
    assert.ok(Array.isArray(entries));
    assert.equal(entries.length, 2);
    const firstEntry: unknown = entries[0];
    const secondEntry: unknown = entries[1];
    assert.ok(typeof firstEntry === "object" && firstEntry !== null && "id" in firstEntry && typeof firstEntry.id === "string");
    assert.ok(typeof secondEntry === "object" && secondEntry !== null && "id" in secondEntry && typeof secondEntry.id === "string");
    assert.doesNotMatch(JSON.stringify(listing), /Private rubric|Elsewhere|never-visible/);
    assert.ok(!JSON.stringify(listing).includes(temporary));

    const selected = await client.callTool({ name: "lighthouse_explorer_snapshot", arguments: { benchmarkId: secondEntry.id } });
    assert.match(JSON.stringify(selected.structuredContent), /Private rubric for two/);
    assert.doesNotMatch(JSON.stringify(selected.structuredContent), /Private rubric for one|never-visible/);
    const trusted = await client.callTool({ name: "lighthouse_explorer_snapshot", arguments: { benchmarkId: secondEntry.id }, _meta: metadata(first) });
    assert.match(JSON.stringify(trusted.structuredContent), /Private rubric for one/);
    assert.doesNotMatch(JSON.stringify(trusted.structuredContent), /Private rubric for two/);
    const invalidHost = await client.callTool({ name: "lighthouse_explorer_snapshot", arguments: { benchmarkId: secondEntry.id }, _meta: { "openai/resource": { path: "" } } });
    assert.equal(invalidHost.isError, true);
    const forged = await client.callTool({ name: "lighthouse_explorer_snapshot", arguments: { benchmarkId: third } });
    assert.equal(forged.isError, true);
    const forbidden = await client.callTool({ name: "lighthouse_explorer_open_evidence", arguments: { path: ".env", benchmarkId: secondEntry.id } });
    assert.equal(forbidden.isError, true);
    assert.doesNotMatch(JSON.stringify(forbidden), /never-visible/);
    const wrongRoot = await client.callTool({ name: "lighthouse_explorer_open_evidence", arguments: { path: "../First/review/one/requirements.md", benchmarkId: secondEntry.id } });
    assert.equal(wrongRoot.isError, true);
    const allowed = await client.callTool({ name: "lighthouse_explorer_open_evidence", arguments: { path: "review/two/requirements.md", benchmarkId: secondEntry.id } });
    assert.deepEqual(allowed.structuredContent, { path: await realpath(join(dirname(second), "review/two/requirements.md")) });

    const attached = await client.callTool({ name: "lighthouse_explorer_attach", arguments: { path: dirname(third) } });
    const attachedPayload: unknown = attached.structuredContent;
    assert.ok(typeof attachedPayload === "object" && attachedPayload !== null && "benchmark" in attachedPayload);
    const added: unknown = attachedPayload.benchmark;
    assert.ok(typeof added === "object" && added !== null && "id" in added && typeof added.id === "string");
    const thirdSnapshot = await client.callTool({ name: "lighthouse_explorer_snapshot", arguments: { benchmarkId: added.id } });
    assert.match(JSON.stringify(thirdSnapshot.structuredContent), /Private rubric for three/);
    assert.doesNotMatch(JSON.stringify(attached), /Private rubric/);
    const listingAgain = await client.callTool({ name: "lighthouse_explorer_library", arguments: {} });
    assert.match(JSON.stringify(listingAgain), /Elsewhere/);
    const deniedAttach = await client.callTool({ name: "lighthouse_explorer_attach", arguments: { path: "relative" } });
    assert.equal(deniedAttach.isError, true);
  } finally {
    await client.close();
    await rm(temporary, { recursive: true, force: true });
  }
});

test("native task authoring binds to the host thread, survives restart, and keeps the selected rubric app-only", async () => {
  const temporary = await mkdtemp(join(tmpdir(), "lighthouse-authoring-mcp-"));
  const firstClient = new Client({ name: "lighthouse-authoring-first-process", version: "1" });
  const restartedClient = new Client({ name: "lighthouse-authoring-restarted-process", version: "1" });
  const home = join(temporary, "home");
  const state = join(temporary, "state");
  const transport = () => new StdioClientTransport({
    command: process.execPath,
    args: [join(root, "dist/server.cjs")],
    env: { LIGHTHOUSE_BENCHMARK_HOME: home, LIGHTHOUSE_AUTHORING_STATE: state },
    stderr: "pipe",
  });
  try {
    const visibleMarker = await benchmark(home, "Discoverable", "visible");
    const privateMarker = await benchmark(temporary, "Elsewhere", "selected");
    const privateRoot = dirname(privateMarker);
    await benchmarkTask(privateRoot, "Elsewhere", "unselected");
    await mkdir(join(privateRoot, "tasks", "selected", "tests"), { recursive: true });
    await mkdir(join(privateRoot, ".local", "drafts", "draft-only", "tests"), { recursive: true });
    await mkdir(join(privateRoot, "review", "draft-only"), { recursive: true });
    await mkdir(join(privateRoot, "review", "review-only"), { recursive: true });
    await Promise.all([
      writeFile(join(privateRoot, "tasks", "selected", "tests", "test.sh"), "Private verifier for selected."),
      writeFile(join(privateRoot, "review", "selected", "qualification.md"), "Private qualification for selected."),
      writeFile(join(privateRoot, "review", "selected", "promotion.json"), JSON.stringify({ qualified: true })),
      writeFile(join(privateRoot, ".local", "drafts", "draft-only", "instruction.md"), "Private draft instructions."),
      writeFile(join(privateRoot, ".local", "drafts", "draft-only", "tests", "test.sh"), "Private draft verifier."),
      writeFile(join(privateRoot, "review", "draft-only", "requirements.md"), "Private draft rubric."),
      writeFile(join(privateRoot, "review", "review-only", "requirements.md"), "Private review-only rubric."),
    ]);
    await firstClient.connect(transport());

    const listed = await firstClient.listTools();
    const opener = listed.tools.find((tool) => tool.name === "lighthouse_open_task_authoring");
    assert.ok(opener);
    assert.deepEqual(opener._meta?.ui, { resourceUri: "ui://lighthouse/explorer-thread" });
    assert.ok(Object.hasOwn(opener.inputSchema.properties ?? {}, "benchmarkPath"));
    assert.ok(!Object.hasOwn(opener.inputSchema.properties ?? {}, "threadId"));
    for (const name of ["lighthouse_explorer_authoring_context", "lighthouse_explorer_authoring_snapshot"]) {
      assert.deepEqual(listed.tools.find((tool) => tool.name === name)?._meta?.ui, { visibility: ["app"] });
    }

    const initialLibrary = await firstClient.callTool({ name: "lighthouse_explorer_library", arguments: {} });
    assert.match(JSON.stringify(initialLibrary), /Discoverable/);
    assert.doesNotMatch(JSON.stringify(initialLibrary), /Elsewhere|Private rubric|never-visible/);
    for (const attempt of [
      { name: opener.name, arguments: { benchmarkPath: dirname(privateMarker), taskId: "selected", threadId: "thread-a" } },
      { name: opener.name, arguments: { benchmarkPath: dirname(privateMarker), taskId: "selected" }, _meta: { threadId: 42 } },
    ]) {
      const denied = await firstClient.callTool(attempt);
      assert.equal(denied.isError, true);
      assert.doesNotMatch(JSON.stringify(denied), /Private rubric|never-visible/);
    }
    const forgedContext = await firstClient.callTool({ name: "lighthouse_explorer_authoring_context", arguments: { threadId: "thread-a" } });
    assert.deepEqual(forgedContext.structuredContent, { focus: null });

    const threadA = { threadId: "thread-a" };
    const threadB = { threadId: "thread-b" };
    const publicResult = await firstClient.callTool({ name: opener.name, arguments: { benchmarkPath: dirname(privateMarker), taskId: "selected", pane: "rubric" }, _meta: threadA });
    const publicPayload: unknown = publicResult.structuredContent;
    assert.ok(typeof publicPayload === "object" && publicPayload !== null && "authoring" in publicPayload);
    const focus = publicPayload.authoring;
    assert.ok(typeof focus === "object" && focus !== null && "benchmarkId" in focus && typeof focus.benchmarkId === "string");
    const benchmarkId = focus.benchmarkId;
    assert.deepEqual(publicPayload, { authoring: { benchmarkId, taskId: "selected", pane: "rubric" } });
    assert.doesNotMatch(JSON.stringify(publicResult), /Private rubric|never-visible/);
    assert.ok(!JSON.stringify(publicResult).includes(temporary));

    const readContext = (client: Client, thread: { threadId: string }) => client.callTool({ name: "lighthouse_explorer_authoring_context", arguments: {}, _meta: thread });
    assert.deepEqual((await readContext(firstClient, threadA)).structuredContent, { focus });
    assert.deepEqual((await readContext(firstClient, threadB)).structuredContent, { focus: null });
    const defaultResult = await firstClient.callTool({ name: opener.name, arguments: { benchmarkPath: privateMarker }, _meta: { threadId: "thread-default" } });
    assert.deepEqual(defaultResult.structuredContent, {
      authoring: { benchmarkId, pane: "instruction" },
      catalog: {
        name: "Elsewhere", published: 2, qualified: 1, drafts: 1, reviewOnly: 1,
        tasks: [
          { id: "draft-only", title: "draft-only", location: "draft", qualification: "unknown" },
          { id: "review-only", title: "review-only", location: "review_only", qualification: "unknown" },
          { id: "selected", title: "Elsewhere/selected", location: "published", qualification: "qualified" },
          { id: "unselected", title: "Elsewhere/unselected", location: "published", qualification: "unknown" },
        ],
      },
    });
    assert.doesNotMatch(JSON.stringify(defaultResult), /Private|Do selected|Do unselected|never-visible/);
    assert.ok(!JSON.stringify(defaultResult).includes(temporary));
    assert.deepEqual((await readContext(firstClient, { threadId: "thread-default" })).structuredContent, { focus: { benchmarkId, pane: "instruction" } });

    const otherResult = await firstClient.callTool({ name: opener.name, arguments: { benchmarkPath: visibleMarker, taskId: "visible", pane: "evaluation" }, _meta: threadB });
    const otherPayload: unknown = otherResult.structuredContent;
    assert.ok(typeof otherPayload === "object" && otherPayload !== null && "authoring" in otherPayload);
    const otherFocus = otherPayload.authoring;
    assert.ok(typeof otherFocus === "object" && otherFocus !== null && "benchmarkId" in otherFocus && typeof otherFocus.benchmarkId === "string");
    assert.notEqual(otherFocus.benchmarkId, benchmarkId);
    assert.deepEqual((await readContext(firstClient, threadA)).structuredContent, { focus });
    assert.deepEqual((await readContext(firstClient, threadB)).structuredContent, { focus: otherFocus });

    await firstClient.close();
    await restartedClient.connect(transport());
    const restartedLibrary = await restartedClient.callTool({ name: "lighthouse_explorer_library", arguments: {} });
    assert.match(JSON.stringify(restartedLibrary), /Discoverable/);
    assert.doesNotMatch(JSON.stringify(restartedLibrary), /Elsewhere/);
    const beforeRestore = await restartedClient.callTool({ name: "lighthouse_explorer_authoring_snapshot", arguments: { benchmarkId, taskId: "selected" } });
    assert.equal(beforeRestore.isError, true);

    const restored = await readContext(restartedClient, threadA);
    assert.deepEqual(restored.structuredContent, { focus });
    assert.doesNotMatch(JSON.stringify(restored), /Private rubric|never-visible/);
    assert.ok(!JSON.stringify(restored).includes(temporary));
    assert.deepEqual((await readContext(restartedClient, threadB)).structuredContent, { focus: otherFocus });
    assert.deepEqual((await readContext(restartedClient, { threadId: "never-opened" })).structuredContent, { focus: null });

    const catalog = await restartedClient.callTool({ name: "lighthouse_explorer_authoring_snapshot", arguments: { benchmarkId } });
    assert.notEqual(catalog.isError, true);
    const catalogSnapshot: unknown = catalog.structuredContent;
    assert.ok(typeof catalogSnapshot === "object" && catalogSnapshot !== null && "tasks" in catalogSnapshot);
    const catalogTasks = catalogSnapshot.tasks;
    assert.ok(Array.isArray(catalogTasks));
    assert.equal(catalogTasks.length, 4);
    for (const item of catalogTasks) {
      const row: unknown = item;
      assert.ok(typeof row === "object" && row !== null && "instruction" in row && "rubric" in row && "qualification" in row && "verifier" in row);
      assert.deepEqual([row.instruction, row.rubric, row.qualification, row.verifier], [null, null, null, null]);
    }
    assert.match(JSON.stringify(catalog), /2 published tasks qualified/);
    assert.doesNotMatch(JSON.stringify(catalog), /Private|Do selected|Do unselected|never-visible/);
    const selected = await restartedClient.callTool({ name: "lighthouse_explorer_authoring_snapshot", arguments: { benchmarkId, taskId: "selected" } });
    assert.notEqual(selected.isError, true);
    assert.match(JSON.stringify(selected.structuredContent), /Private rubric for selected/);
    assert.match(JSON.stringify(selected.structuredContent), /Private verifier for selected/);
    assert.match(JSON.stringify(selected.structuredContent), /Private qualification for selected/);
    assert.match(JSON.stringify(selected.structuredContent), /Do selected/);
    assert.doesNotMatch(JSON.stringify(selected.structuredContent), /Private rubric for unselected|Private rubric for visible|Private draft|Private review-only|never-visible/);
    const unselected = await restartedClient.callTool({ name: "lighthouse_explorer_authoring_snapshot", arguments: { benchmarkId, taskId: "unselected" } });
    assert.match(JSON.stringify(unselected.structuredContent), /Private rubric for unselected/);
    assert.doesNotMatch(JSON.stringify(unselected.structuredContent), /Private rubric for selected|Private rubric for visible|Private draft|Private review-only|never-visible/);
    const file = { name: "benchmark.lighthouse", resourceUri: "host-resource://marker" };
    const trustedMarker = await restartedClient.callTool({ name: "lighthouse_explorer_authoring_snapshot", arguments: { file, benchmarkId: otherFocus.benchmarkId, taskId: "selected" }, _meta: metadata(privateMarker) });
    assert.match(JSON.stringify(trustedMarker.structuredContent), /Private rubric for selected/);
    assert.doesNotMatch(JSON.stringify(trustedMarker.structuredContent), /Private rubric for visible|never-visible/);
  } finally {
    await Promise.allSettled([firstClient.close(), restartedClient.close()]);
    await rm(temporary, { recursive: true, force: true });
  }
});

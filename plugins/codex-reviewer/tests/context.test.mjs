import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { readThreadProjectRoot, resolveProjectRoot } from "../src/server/context.ts";

test("an explicitly injected project wins over the plugin process folder", () => {
  const project = path.resolve("selected-project");
  assert.equal(resolveProjectRoot(project, path.resolve("plugin-cache"), []), project);
});

test("uses the host's project root when the launcher supplies no arguments", () => {
  const project = path.resolve("current-project");
  assert.equal(resolveProjectRoot(undefined, path.resolve("plugin-cache"), [{ uri: pathToFileURL(project).href }]), project);
});

test("keeps the active working folder when several workspace roots exist", () => {
  const project = path.resolve("current-project");
  const roots = [path.resolve("other-project"), project].map((root) => ({ uri: pathToFileURL(root).href }));
  assert.equal(resolveProjectRoot(undefined, path.join(project, "src"), roots), path.join(project, "src"));
});

test("never mistakes the plugin process folder for a project when host context is missing", () => {
  const project = path.resolve("session-project");
  assert.equal(resolveProjectRoot(undefined, project, []), null);
});

test("reads only the exact thread's project metadata through the public app-server protocol", async () => {
  const threadId = "01a13dc3-fc2d-7382-92f9-17456e178a28";
  const project = path.resolve("selected-project");
  const script = `const rl = require('node:readline').createInterface({input:process.stdin});
    rl.on('line', line => { const m=JSON.parse(line);
      if (m.id===1) console.log(JSON.stringify({id:1,result:{}}));
      if (m.id===2) {
        const valid=m.method==='thread/read' && m.params.threadId===${JSON.stringify(threadId)} && m.params.includeTurns===false;
        console.log(JSON.stringify({id:2,result:{thread:{cwd:valid?${JSON.stringify(project)}:null}}}));
      }
    });`;
  assert.equal(await readThreadProjectRoot(threadId, { executable: process.execPath, args: ["-e", script, "--"] }), project);
});

test("rejects malformed thread IDs before starting a backend", async () => {
  await assert.rejects(readThreadProjectRoot("invalid"), /Invalid thread ID/);
});

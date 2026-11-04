import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { hostThreadId, ThreadAuthoringStore } from "./thread-authoring.ts";

test("thread authoring state survives server instances and does not mix conversations", async () => {
  const root = await mkdtemp(path.join(tmpdir(),"lighthouse-authoring-"));
  try {
    const first = new ThreadAuthoringStore(root);
    await first.set("thread/a",{benchmarkPath:"/bench-one",taskId:"task",pane:"rubric"});
    await first.set("thread/b",{benchmarkPath:"/bench-two",pane:"evaluation"});
    const restarted = new ThreadAuthoringStore(root);
    assert.deepEqual(await restarted.get("thread/a"),{benchmarkPath:"/bench-one",taskId:"task",pane:"rubric"});
    assert.deepEqual(await restarted.get("thread/b"),{benchmarkPath:"/bench-two",pane:"evaluation"});
    assert.equal(await restarted.get("unrelated"),null);
    assert.ok((await readdir(root)).every(name=>/^[a-f0-9]{64}\.json$/.test(name)));
  } finally { await rm(root,{recursive:true,force:true}); }
});

test("the conversation identifier is taken only from valid host metadata",()=>{
  assert.equal(hostThreadId({threadId:"trusted"}),"trusted");
  assert.equal(hostThreadId({arguments:{threadId:"untrusted"}}),undefined);
  assert.equal(hostThreadId({threadId:""}),undefined);
});

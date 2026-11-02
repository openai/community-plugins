import assert from "node:assert/strict";
import test from "node:test";
import * as picker from "../src/app/ScopePicker.tsx";

const ref = (name, remote = false) => ({ name, fullName: `refs/${remote ? "remotes" : "heads"}/${name}`, revision: "a".repeat(40) });

test("puts common local and remote branches before alphabetically sorted topic branches", () => {
  assert.equal(typeof picker.filterBranchRefs, "function", "branch filtering is available to the picker");
  const refs = [ref("aaa"), ref("origin/dev", true), ref("develop"), ref("master"), ref("origin/main", true), ref("main"), ref("dev"), ref("zzz"), ref("origin/aaa", true), ref("feature/main")];
  assert.deepEqual(picker.filterBranchRefs(refs, "").map((item) => item.name), [
    "main", "origin/main", "master", "dev", "origin/dev", "develop", "aaa", "feature/main", "origin/aaa", "zzz",
  ]);
  assert.equal(refs[0].name, "aaa", "sorting must not mutate repository data");
});

test("searches complete branch names without case sensitivity and preserves priority", () => {
  assert.equal(typeof picker.filterBranchRefs, "function", "branch filtering is available to the picker");
  const refs = [ref("feature/DOMAIN"), ref("origin/main", true), ref("main"), ref("dev")];
  assert.deepEqual(picker.filterBranchRefs(refs, "  MAIN ").map((item) => item.name), ["main", "origin/main", "feature/DOMAIN"]);
  assert.deepEqual(picker.filterBranchRefs(refs, "ORIGIN/").map((item) => item.name), ["origin/main"]);
  assert.deepEqual(picker.filterBranchRefs(refs, "missing"), []);
  assert.deepEqual(picker.filterBranchRefs([], ""), []);
});

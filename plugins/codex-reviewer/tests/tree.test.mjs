import assert from "node:assert/strict";
import test from "node:test";
import { buildTree, flattenTree } from "../src/app/FileTree.tsx";

test("builds a complete tree, sorts folders first, and propagates changed paths", () => {
  const entries = [
    { path: "README.md", kind: "file", changed: false },
    { path: "src/nested/change.ts", kind: "file", changed: true },
    { path: "src/stable.ts", kind: "file", changed: false },
    { path: "vendor/ignored.txt", kind: "file", changed: false },
  ];
  const tree = buildTree(entries);
  assert.equal(tree[0].path, "src");
  assert.equal(tree[0].changed, true);
  assert.deepEqual(flattenTree(tree, new Set()).map((node) => node.path), ["src", "vendor", "README.md"]);
  const expanded = flattenTree(tree, new Set(["src", "src/nested", "vendor"]));
  assert.ok(expanded.some((node) => node.path === "vendor/ignored.txt"));
  assert.equal(expanded.find((node) => node.path === "src/nested/change.ts").depth, 2);
});

test("a wide directory remains complete when only its visible rows are rendered", () => {
  const entries = Array.from({ length: 10_000 }, (_, index) => ({
    path: `src/file-${index.toString().padStart(5, "0")}.ts`, kind: "file", changed: index === 9999,
  }));
  const rows = flattenTree(buildTree(entries), new Set(["src"]));
  assert.equal(rows.length, 10_001);
  assert.equal(rows.at(-1).path, "src/file-09999.ts");
});

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../plugins/codex-reviewer/", import.meta.url));
const files = ["dist/server/index.js", "dist/app/index.html", "dist/THIRD_PARTY_NOTICES.txt"];
const hashes = () => Promise.all(files.map(async (file) => createHash("sha256").update(await readFile(path.join(root, file))).digest("hex")));
const before = await hashes();
assert.ok(process.env.npm_execpath && path.isAbsolute(process.env.npm_execpath), "Run through npm run test:codex-reviewer.");
const result = spawnSync(process.execPath, [process.env.npm_execpath, "run", "build"], { cwd: root, stdio: "inherit" });
if (result.error) throw result.error;
assert.equal(result.status, 0, "Reviewer build must pass.");
assert.deepEqual(await hashes(), before, "Committed runtime and notices must reproduce exactly from locked source.");
console.log("Reviewer source reproduces all three committed distribution artifacts.");

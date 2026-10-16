import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

export async function createFixtureRepository({ initialCommit = true } = {}) {
  const rootPath = await realpath(await mkdtemp(path.join(tmpdir(), "codex-review-")));

  const git = (...args) => execFileSync("git", args, {
    cwd: rootPath,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trimEnd();
  const write = async (relativePath, content) => {
    const filePath = path.join(rootPath, relativePath);
    await mkdir(path.dirname(filePath), { recursive: true });
    await writeFile(filePath, content, "utf8");
    return filePath;
  };
  const commit = (message) => {
    git("add", "--all");
    git("commit", "-m", message);
    return git("rev-parse", "HEAD");
  };

  execFileSync("git", ["init", "--initial-branch=main", rootPath], { stdio: "ignore" });
  git("config", "user.name", "Fixture User");
  git("config", "user.email", "fixture@example.invalid");
  await write("README.md", "base\n");
  const baseRevision = initialCommit ? commit("base") : null;

  return {
    rootPath,
    git,
    write,
    commit,
    baseRevision,
    cleanup: () => rm(rootPath, { recursive: true, force: true }),
  };
}

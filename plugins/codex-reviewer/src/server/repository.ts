import { realpath, stat } from "node:fs/promises";
import { git } from "./git.js";
import type { RepositoryInfo, RepositoryRef } from "./types.js";

async function listRefs(rootPath: string): Promise<RepositoryRef[]> {
  const output = await git(
    rootPath,
    "for-each-ref",
    "--format=%(refname)%00%(refname:short)%00%(objectname)%00%(symref)",
    "refs/heads",
    "refs/remotes",
  );

  return output.trimEnd().split("\n").filter(Boolean).flatMap((line) => {
    const [fullName, name, revision, symbolicRef] = line.split("\0");
    if (!fullName || !name || !revision || symbolicRef) return [];
    return [{ fullName, name, revision }];
  });
}

export async function listRepositories(selectedPath: string): Promise<RepositoryInfo> {
  if (!selectedPath.trim()) throw new Error("Enter a repository folder path.");

  let selectedRoot: string;
  try {
    selectedRoot = await realpath(selectedPath.trim());
    if (!(await stat(selectedRoot)).isDirectory()) throw new Error();
  } catch {
    throw new Error("The selected folder does not exist or is not a directory.");
  }

  let rootPath: string;
  try {
    rootPath = (await git(selectedRoot, "rev-parse", "--show-toplevel")).trim();
    rootPath = await realpath(rootPath);
  } catch {
    throw new Error("The selected folder is not a Git worktree.");
  }

  let currentBranch: string | null = null;
  try {
    currentBranch = (await git(rootPath, "symbolic-ref", "--quiet", "--short", "HEAD")).trim();
  } catch {
    // Detached HEAD is a valid review state.
  }

  return { rootPath, currentBranch, refs: await listRefs(rootPath) };
}

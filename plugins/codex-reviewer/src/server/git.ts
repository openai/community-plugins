import { execFile } from "node:child_process";
import { promisify } from "node:util";

const executeFile = promisify(execFile);

const gitOptions = (cwd: string) => ({
  cwd,
  maxBuffer: 64 * 1024 * 1024,
  timeout: 30_000,
  windowsHide: true,
  env: {
    ...process.env,
    GIT_OPTIONAL_LOCKS: "0",
    GIT_PAGER: "cat",
    GIT_TERMINAL_PROMPT: "0",
  },
});

function commandError(error: Error & { code?: string | number | null }, stderr: string): Error {
  if (error.code === "ENOENT") return new Error("Git was not found. Install Git and restart Reviewer.");
  return new Error(stderr.trim() || error.message || "Git command failed.");
}

export async function git(cwd: string, ...args: string[]): Promise<string> {
  try {
    const result = await executeFile("git", args, { ...gitOptions(cwd), encoding: "utf8" });
    return result.stdout;
  } catch (error) {
    const failure = error as Error & { code?: string | number | null; stderr?: string };
    throw commandError(failure, failure.stderr ?? "");
  }
}

export async function gitBuffer(cwd: string, ...args: string[]): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    execFile("git", args, { ...gitOptions(cwd), encoding: "buffer" }, (error, stdout, stderr) => {
      if (error) {
        reject(commandError(error, stderr.toString("utf8")));
        return;
      }
      resolve(stdout);
    });
  });
}

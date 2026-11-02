import path from "node:path";
import { fileURLToPath } from "node:url";
import { execFile, spawn } from "node:child_process";
import { access } from "node:fs/promises";
import { createInterface } from "node:readline";
import { promisify } from "node:util";

export function resolveProjectRoot(
  injectedPath: string | undefined,
  workingDirectory: string,
  roots: Array<{ uri: string }>,
): string | null {
  if (injectedPath?.trim()) return path.resolve(injectedPath);

  const folders = roots.flatMap(({ uri }) => {
    try { return uri.startsWith("file:") ? [fileURLToPath(uri)] : []; }
    catch { return []; }
  });
  const current = path.resolve(workingDirectory);
  if (folders.some((folder) => {
    const relative = path.relative(folder, current);
    return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
  })) return current;

  return folders.length === 1 ? folders[0] : null;
}

async function codexExecutable(): Promise<string> {
  if (process.platform !== "win32") return "codex";

  // 1. 复用启动本服务的 Codex 可执行文件，不读取进程参数或用户配置。
  const { stdout } = await promisify(execFile)("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
    `(Get-CimInstance Win32_Process -Filter 'ProcessId = ${process.ppid}').ExecutablePath`], { windowsHide: true, timeout: 3_000 });
  const parent = stdout.trim();
  if (path.basename(parent).toLowerCase() === "codex.exe") return parent;

  for (const folder of (process.env.PATH ?? "").split(path.delimiter)) {
    const candidate = path.join(folder, "codex.exe");
    try { await access(candidate); return candidate; } catch { /* 继续查找公开 CLI。 */ }
  }
  throw new Error("Codex CLI is unavailable");
}

export async function readThreadProjectRoot(
  threadId: string,
  command?: { executable: string; args: string[] },
): Promise<string> {
  if (!/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(threadId)) throw new Error("Invalid thread ID");
  const executable = command?.executable ?? await codexExecutable();
  const child = spawn(executable, [...(command?.args ?? []), "app-server", "--stdio"], {
    windowsHide: true, stdio: ["pipe", "pipe", "ignore"],
  });
  const lines = createInterface({ input: child.stdout });

  // 2. 只请求指定会话的元数据，禁止加载轮次正文；读取后立即关闭临时后端。
  return new Promise<string>((resolve, reject) => {
    let finished = false;
    const finish = (error?: Error, folder?: string) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      lines.close();
      child.stdin.end();
      const shutdown = setTimeout(() => child.kill(), 1_000);
      shutdown.unref();
      child.once("exit", () => clearTimeout(shutdown));
      if (error) reject(error); else resolve(folder!);
    };
    const timer = setTimeout(() => finish(new Error("Project context lookup timed out")), 8_000);
    child.on("error", (error) => finish(error));
    child.stdin.on("error", (error) => finish(error));
    child.on("exit", () => finish(new Error("Project context lookup ended before replying")));
    lines.on("line", (line) => {
      try {
        const message = JSON.parse(line);
        if (message.error) { finish(new Error("Project context is unavailable")); return; }
        if (message.id === 1) {
          child.stdin.write(`${JSON.stringify({ method: "initialized" })}\n`);
          child.stdin.write(`${JSON.stringify({ id: 2, method: "thread/read", params: { threadId, includeTurns: false } })}\n`);
        }
        if (message.id === 2) {
          const folder = message.result?.thread?.cwd;
          if (typeof folder === "string" && path.isAbsolute(folder)) finish(undefined, folder);
          else finish(new Error("Project context is unavailable"));
        }
      } catch { finish(new Error("Invalid project context response")); }
    });
    child.stdin.write(`${JSON.stringify({ id: 1, method: "initialize", params: {
      clientInfo: { name: "git-reviewer-context", version: "0.3.1" }, capabilities: { experimentalApi: true },
    } })}\n`);
  });
}

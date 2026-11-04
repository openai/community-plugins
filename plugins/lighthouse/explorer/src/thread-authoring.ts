import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import { z } from "zod";

export const authoringPane = z.enum(["instruction", "rubric", "evaluation"]);
const bindingSchema = z.object({benchmarkPath: z.string().min(1), taskId: z.string().optional(), pane: authoringPane});
export type AuthoringBinding = z.infer<typeof bindingSchema>;

export function hostThreadId(meta: unknown): string | undefined {
  if (typeof meta !== "object" || meta === null || !("threadId" in meta)) return undefined;
  return typeof meta.threadId === "string" && meta.threadId.length > 0 ? meta.threadId : undefined;
}

export class ThreadAuthoringStore {
  readonly #root: string;

  constructor(root = process.env.LIGHTHOUSE_AUTHORING_STATE ?? path.join(homedir(), ".codex", "lighthouse-explorer", "authoring")) { this.#root = root; }

  async get(threadId: string): Promise<AuthoringBinding | null> {
    try { return bindingSchema.parse(JSON.parse(await readFile(this.#file(threadId), "utf8"))); }
    catch (error) {
      if (error instanceof Error && "code" in error && error.code === "ENOENT") return null;
      throw error;
    }
  }

  async set(threadId: string, binding: AuthoringBinding): Promise<void> {
    const value = bindingSchema.parse(binding);
    await mkdir(this.#root, {recursive: true, mode: 0o700});
    const target = this.#file(threadId);
    const temporary = `${target}.${randomUUID()}.tmp`;
    await writeFile(temporary, JSON.stringify(value), {mode: 0o600});
    await rename(temporary, target);
  }

  #file(threadId: string): string {
    return path.join(this.#root, `${createHash("sha256").update(threadId).digest("hex")}.json`);
  }
}

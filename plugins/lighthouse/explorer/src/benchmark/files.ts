import { constants } from "node:fs";
import { open, readdir, realpath } from "node:fs/promises";
import path from "node:path";

const MAX_BYTES = 3 * 1024 * 1024;
const MARKER = "benchmark.lighthouse";

export type JsonObject = Record<string, unknown>;

export function object(value: unknown): JsonObject | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? Object.fromEntries(Object.entries(value)) : null;
}

export function string(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export function child(value: JsonObject | null, name: string): JsonObject | null {
  return object(value?.[name]);
}

export function inside(root: string, target: string): boolean {
  const relative = path.relative(root, target);
  return relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

export function validTaskId(id: string): boolean {
  return /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(id) && id !== "." && id !== "..";
}

function draftArtifact(parts: string[]): boolean {
  if (parts.length === 1) return parts[0] === "instruction.md" || parts[0] === "task.toml";
  return parts.length === 2 && parts[0] === "tests" && parts[1] === "test.sh";
}

function expected(relative: string): boolean {
  const segments = relative.split("/");
  if (segments[0] === ".local") {
    const [, container, task, ...artifacts] = segments;
    return container === "drafts" && task !== undefined && validTaskId(task)
      && !/^(?:secrets?|credentials?|private[-_]?keys?)$/i.test(task) && draftArtifact(artifacts);
  }
  if (segments.some((segment) => !segment || segment === "." || segment === ".." || segment.startsWith("."))) return false;
  if (segments.slice(0, -1).some((segment) => /^(?:secrets?|credentials?|private[-_]?keys?)$/i.test(segment))) return false;
  if (/(?:^|[-_.])(?:credentials?|secrets?|(?:access|refresh|api)[-_]?tokens?|passwords?|private[-_]?keys?)(?:[-_.]|$)/i.test(segments.at(-1) ?? "")) return false;
  const [section, task, ...rest] = segments;
  if (section === MARKER && segments.length === 1) return true;
  if (section === "README.md" && segments.length === 1) return true;
  if (section === "leaderboard") return rest.length === 0 && /^(?:README|index)\.md$/i.test(task ?? "");
  if (section === "tasks") return (rest.length === 1 && /^(?:task\.toml|instruction\.md|README\.md)$/.test(rest[0] ?? "")) || (rest.length === 2 && rest[0] === "tests" && rest[1] === "test.sh");
  if (section === "review") return rest.length >= 1 && /\.(?:md|json)$/.test(rest.at(-1) ?? "");
  if (section === "experiments") return rest.length >= 1 && /(?:\.md|(?:quality-index|summary|correction-results|final-summary)\.json)$/.test(rest.at(-1) ?? "");
  if (section === "jobs" || section === "regrades") {
    const filename = rest.at(-1) ?? "";
    if (filename === "result.json") return true;
    const parent = rest.at(-2);
    return parent === "verifier" && /\.(?:md|txt|log|json|png)$/.test(filename);
  }
  return false;
}

function expectedDirectory(relative: string): boolean {
  if (relative === ".local/drafts" || relative === "review") return true;
  if (!/^(tasks|jobs|regrades|experiments)(\/[^/]+)*$/.test(relative)) return false;
  return !relative.split("/").some((segment) => segment.startsWith(".") || /^(?:secrets?|credentials?|private[-_]?keys?)$/i.test(segment));
}

export function pathFromIndex(indexPath: string, reference: string): string | null {
  if (path.isAbsolute(reference) || reference.includes("\\") || reference.includes("\0")) return null;
  const normalized = path.posix.normalize(path.posix.join(path.posix.dirname(indexPath), reference));
  return normalized.startsWith("../") ? null : normalized;
}

export function configuredPath(value: string): string | null {
  if (value.includes("\\") || value.includes("\0") || path.posix.isAbsolute(value)) return null;
  return value.split("/").some((segment) => segment === ".." || segment === "." || !segment) ? null : value;
}

function missing(error: unknown): boolean {
  return error instanceof Error && "code" in error && (error.code === "ENOENT" || error.code === "ENOTDIR");
}

export class BenchmarkFiles {
  readonly root: string;
  readonly marker: string;

  private constructor(root: string, marker: string) {
    this.root = root;
    this.marker = marker;
  }

  static async fromMarker(marker: string): Promise<BenchmarkFiles> {
    if (!path.isAbsolute(marker) || path.basename(marker) !== MARKER) throw new Error("Open an absolute benchmark.lighthouse launcher to choose a benchmark.");
    const root = await realpath(path.dirname(marker));
    const actual = await realpath(marker);
    if (actual !== path.join(root, MARKER)) throw new Error("The launcher may not point outside its benchmark directory.");
    return new BenchmarkFiles(root, actual);
  }

  async locate(relative: string): Promise<string | null> {
    if (!configuredPath(relative) || !expected(relative)) throw new Error("This file is outside the Explorer evidence allowlist.");
    const candidate = path.join(this.root, ...relative.split("/"));
    try {
      const actual = await realpath(candidate);
      if (!inside(this.root, actual)) throw new Error("This evidence points outside the selected benchmark.");
      const normalized = path.relative(this.root, actual).split(path.sep).join("/");
      if (!expected(normalized)) throw new Error("This evidence resolves to a file outside the Explorer evidence allowlist.");
      const handle = await open(actual, constants.O_RDONLY | constants.O_NOFOLLOW);
      try {
        return (await handle.stat()).isFile() ? actual : null;
      } finally {
        await handle.close();
      }
    } catch (error) {
      if (missing(error)) return null;
      throw error;
    }
  }

  async read(relative: string): Promise<string | null> {
    const actual = await this.locate(relative);
    if (!actual) return null;
    const handle = await open(actual, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
      const buffer = Buffer.alloc(MAX_BYTES + 1);
      let used = 0;
      while (used < buffer.byteLength) {
        const { bytesRead } = await handle.read(buffer, used, buffer.byteLength - used, used);
        if (!bytesRead) break;
        used += bytesRead;
      }
      if (used > MAX_BYTES) throw new Error("This evidence exceeds the Explorer's read limit.");
      return buffer.subarray(0, used).toString("utf8");
    } finally {
      await handle.close();
    }
  }

  async json(relative: string): Promise<JsonObject | null> {
    const content = await this.read(relative);
    if (content === null) return null;
    let parsed: unknown;
    try {
      parsed = JSON.parse(content);
    } catch {
      throw new Error(`Invalid JSON evidence: ${relative}`);
    }
    const result = object(parsed);
    if (!result) throw new Error(`Expected a JSON object: ${relative}`);
    return result;
  }

  async directories(relative: string): Promise<string[]> {
    if (!configuredPath(relative) || !expectedDirectory(relative)) throw new Error("This is not an Explorer evidence directory.");
    const candidate = path.join(this.root, ...relative.split("/"));
    try {
      const actual = await realpath(candidate);
      if (!inside(this.root, actual)) throw new Error("This directory points outside the selected benchmark.");
      const normalized = path.relative(this.root, actual).split(path.sep).join("/");
      if (!expectedDirectory(normalized)) throw new Error("This directory resolves outside the Explorer evidence directory allowlist.");
      const entries = await readdir(actual, { withFileTypes: true });
      return entries.filter((entry) => entry.isDirectory() || entry.isSymbolicLink()).map((entry) => entry.name).sort();
    } catch (error) {
      if (missing(error)) return [];
      throw error;
    }
  }
}

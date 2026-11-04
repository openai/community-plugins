import { createHash } from "node:crypto";
import { readdir } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import { BenchmarkFiles } from "./benchmark/files.ts";
import { manifest } from "./benchmark/manifest.ts";

const MARKER = "benchmark.lighthouse";

export interface LibraryBenchmark {
  id: string;
  name: string;
  rootLabel: string;
}

export interface LibraryListing {
  benchmarks: LibraryBenchmark[];
  preferredId?: string;
}

interface LibraryOptions {
  paths?: readonly string[];
  home?: string;
  conventionalNamesOnly?: boolean;
}

interface RegisteredBenchmark {
  marker: string;
  benchmark: LibraryBenchmark;
}

function hasFilesystemCode(error: unknown, codes: readonly string[]): boolean {
  return error instanceof Error && "code" in error && typeof error.code === "string" && codes.includes(error.code);
}

function markerAt(location: string): string {
  if (!path.isAbsolute(location) || location.includes("\0") || location.split(path.sep).includes("..")) {
    throw new Error("Choose an absolute benchmark folder or its benchmark.lighthouse file.");
  }
  return path.basename(location) === MARKER ? location : path.join(location, MARKER);
}

function idFor(marker: string): string {
  return `benchmark_${createHash("sha256").update(marker).digest("base64url")}`;
}

export function libraryOptionsFromEnvironment(environment: NodeJS.ProcessEnv, userHome = homedir()): LibraryOptions {
  const paths = environment.LIGHTHOUSE_BENCHMARKS?.split(path.delimiter).filter(Boolean) ?? [];
  const configuredHome = environment.LIGHTHOUSE_BENCHMARK_HOME;
  return {
    paths,
    home: configuredHome ?? path.join(userHome, "code"),
    conventionalNamesOnly: configuredHome === undefined,
  };
}

export class BenchmarkLibrary {
  readonly #options: LibraryOptions;
  readonly #benchmarks = new Map<string, RegisteredBenchmark>();
  #preferred: string | undefined;
  #initializing: Promise<void> | undefined;

  constructor(options: LibraryOptions = {}) {
    this.#options = options;
  }

  async list(): Promise<LibraryListing> {
    await this.#initialize();
    const benchmarks = [...this.#benchmarks.values()].map((entry) => entry.benchmark)
      .sort((first, second) => first.name.localeCompare(second.name) || first.rootLabel.localeCompare(second.rootLabel));
    const preferredId = this.#preferred ?? (benchmarks.length === 1 ? benchmarks[0]?.id : undefined);
    return { benchmarks, ...(preferredId ? { preferredId } : {}) };
  }

  async attach(location: string): Promise<LibraryBenchmark> {
    await this.#initialize();
    return this.#register(location);
  }

  async resolve(id: string): Promise<string> {
    await this.#initialize();
    const selected = this.#benchmarks.get(id);
    if (!selected) throw new Error("Choose a known benchmark in Lighthouse before opening its evidence.");
    return selected.marker;
  }

  #initialize(): Promise<void> {
    this.#initializing ??= this.#seed();
    return this.#initializing;
  }

  async #seed(): Promise<void> {
    const explicit = new Set<string>();
    for (const location of this.#options.paths ?? []) {
      try { explicit.add((await this.#register(location)).id); } catch { /* An unavailable configured benchmark remains attachable once it returns. */ }
    }
    if (explicit.size === 1) this.#preferred = [...explicit][0];

    const home = this.#options.home;
    if (!home) return;
    if (!this.#options.conventionalNamesOnly) {
      try { await this.#register(home); } catch { /* The home usually contains checkouts; it need not itself be a benchmark. */ }
    }
    try {
      const entries = await readdir(home, { withFileTypes: true });
      for (const entry of entries.sort((first, second) => first.name.localeCompare(second.name))) {
        if (!entry.isDirectory() || entry.name.startsWith(".") || (this.#options.conventionalNamesOnly && !entry.name.includes("-bench"))) continue;
        try { await this.#register(path.join(home, entry.name)); } catch { /* Only directories with valid exact markers belong in the library. */ }
      }
    } catch (error) {
      if (!hasFilesystemCode(error, ["ENOENT", "ENOTDIR", "EACCES", "EPERM"])) throw error;
    }
  }

  async #register(location: string): Promise<LibraryBenchmark> {
    let files: BenchmarkFiles;
    try {
      files = await BenchmarkFiles.fromMarker(markerAt(location));
    } catch (error) {
      if (hasFilesystemCode(error, ["ENOENT", "ENOTDIR", "EACCES", "EPERM"])) {
        throw new Error("No readable benchmark.lighthouse file was found at that location.");
      }
      throw error;
    }
    const id = idFor(files.marker);
    const existing = this.#benchmarks.get(id);
    if (existing) return existing.benchmark;

    const config = await manifest(files);
    const benchmark = { id, name: config.name, rootLabel: path.basename(files.root) };
    this.#benchmarks.set(id, { marker: files.marker, benchmark });
    return benchmark;
  }
}

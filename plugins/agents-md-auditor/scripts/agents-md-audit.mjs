#!/usr/bin/env node

// Deterministic, read-only checks for AGENTS.md instruction files.
// This script never writes, never follows symlinks, never runs the commands
// it inspects, and never contacts the network.

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

export const TOOL_VERSION = "0.1.0";
export const DEFAULT_MAX_BYTES = 32 * 1024;
export const DEFAULT_MAX_FILES = 200;
const MAX_READ_BYTES = 1024 * 1024;

export const INSTRUCTION_FILES = ["AGENTS.md", "AGENTS.override.md"];

export const RULES = {
  "unknown-script": {
    severity: "error",
    summary: "A documented npm/pnpm/yarn/bun script is not defined in the nearest package.json.",
  },
  "no-package-json": {
    severity: "error",
    summary: "A package script is documented but no package.json exists between the file and the repository root.",
  },
  "unknown-make-target": {
    severity: "error",
    summary: "A documented make target is not defined in the nearest Makefile.",
  },
  "no-makefile": {
    severity: "error",
    summary: "A make target is documented but no Makefile exists between the file and the repository root.",
  },
  "broken-link": {
    severity: "error",
    summary: "A relative Markdown link points to a file or directory that does not exist.",
  },
  "secret-like": {
    severity: "error",
    summary: "The file contains a credential-shaped value. Instruction files are sent to the model and usually committed.",
  },
  "broken-path": {
    severity: "warning",
    summary: "An inline-code path does not exist relative to the file or the repository root.",
  },
  "package-manager-mismatch": {
    severity: "warning",
    summary: "Commands use a package manager that differs from the repository lockfile.",
  },
  "oversize-file": {
    severity: "warning",
    summary: "A single instruction file is larger than the byte budget.",
  },
  "oversize-chain": {
    severity: "warning",
    summary: "Instructions combined from the repository root down to this directory exceed the byte budget.",
  },
  "duplicate-heading": {
    severity: "warning",
    summary: "The same heading appears more than once in one file.",
  },
  "empty-file": {
    severity: "warning",
    summary: "The instruction file has no content.",
  },
  "override-shadows": {
    severity: "info",
    summary: "AGENTS.override.md and AGENTS.md share a directory; the override is used instead of AGENTS.md there.",
  },
  "repeated-from-parent": {
    severity: "info",
    summary: "A line repeats an instruction already present in a parent directory's file.",
  },
  "vague-instruction": {
    severity: "info",
    summary: "A generic instruction gives the agent nothing concrete to act on.",
  },
  "missing-root": {
    severity: "info",
    summary: "Nested instruction files exist but the repository root has none.",
  },
  "path-outside-root": {
    severity: "info",
    summary: "A reference resolves outside the audited root and was not inspected.",
  },
  "discovery-limit": {
    severity: "warning",
    summary: "Discovery stopped at the file limit; some instruction files were not audited.",
  },
};

const SEVERITY_ORDER = { error: 3, warning: 2, info: 1 };

const SKIPPED_DIRECTORIES = new Set([
  ".git",
  ".hg",
  ".svn",
  "node_modules",
  ".pnpm-store",
  ".yarn",
  "bower_components",
  "vendor",
  "dist",
  "build",
  "out",
  "target",
  "coverage",
  ".next",
  ".nuxt",
  ".turbo",
  ".cache",
  ".venv",
  "venv",
  "__pycache__",
  ".tox",
  ".gradle",
  "DerivedData",
]);

const KNOWN_EXTENSIONS = new Set(
  (
    "md mdx markdown json jsonc json5 yaml yml toml ini cfg conf js mjs cjs jsx ts mts cts tsx " +
    "py pyi rb go rs java kt kts scala swift m mm c h cc cpp cxx hpp cs fs php pl lua dart ex exs " +
    "sh bash zsh fish ps1 bat cmd sql graphql gql proto html htm css scss sass less vue svelte astro " +
    "txt rst lock xml gradle tf tfvars hcl csv ipynb wasm env"
  ).split(" "),
);

const KNOWN_BARE_FILES = new Set([
  "Dockerfile",
  "Makefile",
  "GNUmakefile",
  "Gemfile",
  "Rakefile",
  "Procfile",
  "Justfile",
  "Taskfile",
  "LICENSE",
  "CODEOWNERS",
]);

const VAGUE_PHRASES = [
  "follow best practices",
  "follow good practices",
  "write clean code",
  "write good code",
  "write high quality code",
  "write high-quality code",
  "keep the code clean",
  "keep code clean",
  "be careful",
  "use common sense",
  "use good judgment",
  "use good judgement",
  "make sure everything works",
  "do not make mistakes",
  "don't make mistakes",
];

// Value-shaped patterns only: a prefix followed by enough token characters.
const SECRET_PATTERNS = [
  ["GitHub token", /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b/u],
  ["GitHub fine-grained token", /\bgithub_pat_[A-Za-z0-9_]{40,}\b/u],
  ["API secret key", /\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_-]{32,}\b/u],
  ["AWS access key", /\bAKIA[0-9A-Z]{16}\b/u],
  ["Slack token", /\bxox[abprs]-[A-Za-z0-9-]{20,}\b/u],
  ["private key block", /-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----/u],
];

const WORKSPACE_FLAGS = new Set([
  "--filter",
  "-F",
  "--workspace",
  "-w",
  "--workspaces",
  "-ws",
  "--prefix",
  "-C",
  "--cwd",
  "--dir",
  "-r",
  "--recursive",
  "workspace",
  "workspaces",
]);

const SHELL_FENCE_LANGUAGES = new Set(["", "sh", "bash", "shell", "zsh", "console", "terminal", "fish"]);

const LOCKFILES = [
  ["pnpm-lock.yaml", "pnpm"],
  ["yarn.lock", "yarn"],
  ["bun.lockb", "bun"],
  ["bun.lock", "bun"],
  ["package-lock.json", "npm"],
  ["npm-shrinkwrap.json", "npm"],
];

const MAKEFILE_NAMES = ["GNUmakefile", "makefile", "Makefile"];

function toPosix(relativePath) {
  return relativePath.split(path.sep).join("/");
}

function isInside(parent, child) {
  const relative = path.relative(parent, child);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

// Directory listings are cached per audit. Existence checks compare exact
// names so results match case-sensitive filesystems (and CI) on macOS/Windows.
const listingCache = new Map();
let listingRoot = null;

function listing(directory) {
  if (!listingCache.has(directory)) {
    let names = null;
    try {
      names = new Set(fs.readdirSync(directory));
    } catch {
      names = null;
    }
    listingCache.set(directory, names);
  }
  return listingCache.get(directory);
}

function lexists(target) {
  const resolved = path.resolve(target);
  if (!listingRoot || !isInside(listingRoot, resolved)) {
    try {
      fs.lstatSync(resolved);
      return true;
    } catch {
      return false;
    }
  }
  let current = listingRoot;
  for (const segment of path.relative(listingRoot, resolved).split(path.sep).filter(Boolean)) {
    const names = listing(current);
    if (!names || !names.has(segment)) return false;
    current = path.join(current, segment);
  }
  return true;
}

function lstatExact(target) {
  if (!lexists(target)) return null;
  try {
    return fs.lstatSync(target);
  } catch {
    return null;
  }
}

function isRegularFile(target) {
  return lstatExact(target)?.isFile() ?? false;
}

function isDirectory(target) {
  return lstatExact(target)?.isDirectory() ?? false;
}

function readTextLimited(filePath) {
  const fd = fs.openSync(filePath, "r");
  try {
    const buffer = Buffer.alloc(MAX_READ_BYTES);
    const bytesRead = fs.readSync(fd, buffer, 0, MAX_READ_BYTES, 0);
    return buffer.subarray(0, bytesRead).toString("utf8");
  } finally {
    fs.closeSync(fd);
  }
}

export function discoverInstructionFiles(rootDir, maxFiles = DEFAULT_MAX_FILES) {
  const found = [];
  let truncated = false;
  const stack = [rootDir];

  while (stack.length > 0) {
    const directory = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(directory, { withFileTypes: true });
    } catch {
      continue;
    }
    entries.sort((a, b) => a.name.localeCompare(b.name));
    const childDirectories = [];

    for (const entry of entries) {
      const entryPath = path.join(directory, entry.name);
      // Dirent types come from lstat semantics: symlinks are never followed.
      if (entry.isSymbolicLink()) {
        continue;
      }
      if (entry.isDirectory()) {
        if (!SKIPPED_DIRECTORIES.has(entry.name)) {
          childDirectories.push(entryPath);
        }
        continue;
      }
      if (entry.isFile() && INSTRUCTION_FILES.includes(entry.name)) {
        if (found.length >= maxFiles) {
          truncated = true;
          continue;
        }
        found.push(entryPath);
      }
    }

    for (const child of childDirectories.reverse()) {
      stack.push(child);
    }
  }

  found.sort((a, b) => toPosix(path.relative(rootDir, a)).localeCompare(toPosix(path.relative(rootDir, b))));
  return { files: found, truncated };
}

export function scanMarkdown(text) {
  const lines = text.split(/\r?\n/u);
  const result = { lines, inlineCode: [], links: [], commandLines: [], headings: [], proseLines: [] };
  let fence = null;

  lines.forEach((line, index) => {
    const lineNumber = index + 1;
    const fenceMatch = line.match(/^\s{0,3}(`{3,}|~{3,})\s*([\w+-]*)/u);
    if (fence) {
      if (fenceMatch && fenceMatch[1][0] === fence.marker[0] && fenceMatch[1].length >= fence.marker.length && line.trim() === fenceMatch[1]) {
        fence = null;
      } else if (fence.shell) {
        const command = line.trim().startsWith("#") ? "" : line.replace(/^\s*(?:\$|>)\s+/u, "").trim();
        if (command) {
          result.commandLines.push({ line: lineNumber, text: command });
        }
      }
      return;
    }
    if (fenceMatch) {
      fence = { marker: fenceMatch[1], shell: SHELL_FENCE_LANGUAGES.has(fenceMatch[2].toLowerCase()) };
      return;
    }

    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/u);
    if (heading) {
      result.headings.push({ line: lineNumber, level: heading[1].length, text: heading[2] });
    }
    result.proseLines.push({ line: lineNumber, text: line });

    for (const match of line.matchAll(/`([^`\n]+)`/gu)) {
      result.inlineCode.push({ line: lineNumber, text: match[1].trim() });
    }
    const withoutCode = line.replace(/`[^`\n]*`/gu, "");
    for (const match of withoutCode.matchAll(/!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/gu)) {
      result.links.push({ line: lineNumber, target: match[1] });
    }
  });

  return result;
}

export function isPathCandidate(token, baseDir, rootDir) {
  let value = token.trim().replace(/:\d+(?::\d+)?$/u, "");
  if (!value || value.length > 300) return null;
  if (/[\s*?<>{}$|`"'()=,;!\\[\]]/u.test(value)) return null;
  if (/^(?:[a-z][a-z0-9+.-]*:)/iu.test(value)) return null;
  if (/^[/~@#-]/u.test(value)) return null;
  if (/^\.+$/u.test(value)) return null;
  if (/^v?\d+(?:\.\d+)+/u.test(value)) return null;

  const explicit = /^\.{1,2}\//u.test(value);
  const trimmed = value.replace(/\/+$/u, "");
  const segments = trimmed.split("/").filter(Boolean);
  if (segments.length === 0) return null;
  const last = segments[segments.length - 1];
  const extension = last.includes(".") && !last.startsWith(".") ? last.split(".").pop().toLowerCase() : "";
  const hasKnownExtension = KNOWN_EXTENSIONS.has(extension);
  const hasSlash = segments.length > 1;
  // Bare names (`index.js`) are usually generic mentions, not references.
  if (!explicit && !hasSlash) return null;
  // Build output and dependency directories are expected to be absent.
  if (segments.some((segment) => SKIPPED_DIRECTORIES.has(segment))) return null;
  const first = segments[0];
  const firstExists =
    !explicit && (lexists(path.join(baseDir, first)) || lexists(path.join(rootDir, first)));

  if (explicit || hasKnownExtension || KNOWN_BARE_FILES.has(last) || firstExists) {
    return value;
  }
  return null;
}

export function splitCommandSegments(text) {
  return text
    .split(/&&|\|\||;|\|/u)
    .map((segment) => segment.trim())
    .filter(Boolean);
}

function cleanToken(token) {
  return token.replace(/^["']|["'.,)]+$/gu, "");
}

export function parsePackageScript(segment) {
  const match = segment.match(/(?:^|\s)(npm|pnpm|yarn|bun)\s+(.+)$/u);
  if (!match) return null;
  const manager = match[1];
  const tokens = match[2].split(/\s+/u).map(cleanToken).filter(Boolean);
  if (tokens.some((token) => WORKSPACE_FLAGS.has(token) || token.startsWith("--filter=") || token.startsWith("--workspace=") || token.startsWith("--prefix=") || token.startsWith("--cwd=") || token.startsWith("--dir="))) {
    return { manager, script: null, verb: tokens[0] ?? null, workspace: true };
  }
  const verb = tokens[0] ?? null;
  let script = null;
  if (verb === "run" || verb === "run-script") {
    const candidate = tokens.slice(1).find((token) => !token.startsWith("-"));
    if (candidate && candidate !== "--" && !/[<>{}$*]/u.test(candidate)) script = candidate;
  } else if (manager === "npm" && ["test", "t", "start", "stop", "restart"].includes(verb)) {
    script = verb === "t" ? "test" : verb;
  }
  return { manager, script, verb, workspace: false };
}

export function parseMakeInvocation(segment) {
  const match = segment.match(/(?:^|\s)make(?:\s+(.*))?$/u);
  if (!match) return null;
  const tokens = (match[1] ?? "").split(/\s+/u).map(cleanToken).filter(Boolean);
  const targets = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "-C" || token === "-f" || token === "--file" || token === "--directory" || token.startsWith("--directory=") || token.startsWith("--file=")) {
      return { targets: [], redirected: true };
    }
    if (token.startsWith("-") || token.includes("=")) continue;
    if (!/^[A-Za-z0-9_.\/-]+$/u.test(token)) continue;
    targets.push(token);
  }
  return { targets, redirected: false };
}

export function parseMakeTargets(text) {
  const targets = new Set();
  let hasInclude = false;
  let hasPatternRule = false;
  for (const rawLine of text.split(/\r?\n/u)) {
    if (rawLine.startsWith("\t")) continue;
    const line = rawLine.replace(/#.*$/u, "");
    if (/^\s*-?(?:include|sinclude)\s/u.test(line)) {
      hasInclude = true;
      continue;
    }
    const match = line.match(/^([^:=#\t][^:=]*?)\s*::?(?!=)(.*)$/u);
    if (!match) continue;
    const names = match[1].trim().split(/\s+/u);
    if (names.length === 1 && names[0] === ".PHONY") {
      for (const name of match[2].trim().split(/\s+/u).filter(Boolean)) targets.add(name);
      continue;
    }
    for (const name of names) {
      if (name.includes("$")) continue;
      if (name.includes("%")) {
        hasPatternRule = true;
        continue;
      }
      if (name.startsWith(".")) continue;
      targets.add(name);
    }
  }
  return { targets, hasInclude, hasPatternRule };
}

function findUpward(startDir, rootDir, names) {
  let current = startDir;
  while (isInside(rootDir, current)) {
    for (const name of names) {
      const candidate = path.join(current, name);
      if (isRegularFile(candidate)) return candidate;
    }
    if (current === rootDir) break;
    current = path.dirname(current);
  }
  return null;
}

function detectLockManagers(startDir, rootDir) {
  let current = startDir;
  while (isInside(rootDir, current)) {
    const managers = new Set();
    const files = [];
    for (const [file, manager] of LOCKFILES) {
      if (isRegularFile(path.join(current, file))) {
        managers.add(manager);
        files.push(file);
      }
    }
    if (managers.size > 0) return { managers, files };
    if (current === rootDir) break;
    current = path.dirname(current);
  }
  return { managers: new Set(), files: [] };
}

function normalizeLine(text) {
  return text
    .trim()
    .replace(/^(?:[-*+]|\d+[.)])\s+/u, "")
    .replace(/\s+/gu, " ")
    .toLowerCase();
}

function redact(value) {
  return value.slice(0, 4) + "…[redacted " + (value.length - 4) + " chars]";
}

class Context {
  constructor(rootDir, cache) {
    this.rootDir = rootDir;
    this.cache = cache;
  }

  packageScripts(packagePath) {
    if (!this.cache.packages.has(packagePath)) {
      let scripts = null;
      try {
        const parsed = JSON.parse(readTextLimited(packagePath));
        scripts = parsed && typeof parsed.scripts === "object" && parsed.scripts ? Object.keys(parsed.scripts) : [];
      } catch {
        scripts = null;
      }
      this.cache.packages.set(packagePath, scripts);
    }
    return this.cache.packages.get(packagePath);
  }

  makeTargets(makefilePath) {
    if (!this.cache.makefiles.has(makefilePath)) {
      this.cache.makefiles.set(makefilePath, parseMakeTargets(readTextLimited(makefilePath)));
    }
    return this.cache.makefiles.get(makefilePath);
  }
}

function checkCommands(candidate, fileDir, context, report) {
  const { rootDir } = context;
  let baseDir = fileDir;
  for (const segment of splitCommandSegments(candidate.text)) {
    const cd = segment.match(/^cd\s+(\S+)$/u);
    if (cd) {
      const target = path.resolve(baseDir, cleanToken(cd[1]));
      if (!isInside(rootDir, target) || !isDirectory(target)) return;
      baseDir = target;
      continue;
    }

    const pkg = parsePackageScript(segment);
    if (pkg) {
      if (["install", "i", "ci", "add", "run", "run-script"].includes(pkg.verb)) {
        report.managerUse(pkg.manager, candidate.line, baseDir);
      }
      if (pkg.script && !pkg.workspace) {
        const packagePath = findUpward(baseDir, rootDir, ["package.json"]);
        if (!packagePath) {
          report.add("no-package-json", candidate.line, "`" + segment + "` runs script \"" + pkg.script + "\" but no package.json was found up to the repository root.");
        } else {
          const scripts = context.packageScripts(packagePath);
          if (scripts && !scripts.includes(pkg.script)) {
            const where = toPosix(path.relative(rootDir, packagePath));
            const available = scripts.length > 0 ? scripts.slice(0, 10).join(", ") : "none";
            report.add("unknown-script", candidate.line, "`" + segment + "`: no \"" + pkg.script + "\" script in " + where + " (available: " + available + ").");
          }
        }
      }
      continue;
    }

    const make = parseMakeInvocation(segment);
    if (make && !make.redirected && make.targets.length > 0) {
      const makefilePath = findUpward(baseDir, rootDir, MAKEFILE_NAMES);
      if (!makefilePath) {
        report.add("no-makefile", candidate.line, "`" + segment + "` but no Makefile was found up to the repository root.");
        continue;
      }
      const parsed = context.makeTargets(makefilePath);
      for (const target of make.targets) {
        if (!parsed.targets.has(target)) {
          const where = toPosix(path.relative(rootDir, makefilePath));
          const uncertain = parsed.hasInclude || parsed.hasPatternRule;
          report.add(
            "unknown-make-target",
            candidate.line,
            "`" + segment + "`: target \"" + target + "\" is not defined in " + where + (uncertain ? " (it may come from an include or pattern rule)." : "."),
            uncertain ? "warning" : undefined,
          );
        }
      }
    }
  }
}

function checkFile(filePath, text, context, report) {
  const { rootDir } = context;
  const fileDir = path.dirname(filePath);
  const scan = scanMarkdown(text);

  if (text.trim() === "") {
    report.add("empty-file", 1, "The file is empty; remove it or add project-specific instructions.");
    return scan;
  }

  for (const candidate of [...scan.inlineCode, ...scan.commandLines]) {
    checkCommands(candidate, fileDir, context, report);
  }

  for (const code of scan.inlineCode) {
    const candidate = isPathCandidate(code.text, fileDir, rootDir);
    if (!candidate) continue;
    const fromFile = path.resolve(fileDir, candidate);
    const fromRoot = path.resolve(rootDir, candidate);
    const explicit = /^\.{1,2}\//u.test(candidate);
    const options = explicit ? [fromFile] : [fromFile, fromRoot];
    const inside = options.filter((option) => isInside(rootDir, option));
    if (inside.length === 0) {
      report.add("path-outside-root", code.line, "`" + candidate + "` resolves outside the audited root and was not checked.");
      continue;
    }
    if (!inside.some((option) => lexists(option))) {
      report.add("broken-path", code.line, "`" + candidate + "` does not exist relative to this file or the repository root.");
    }
  }

  for (const link of scan.links) {
    let target = link.target;
    if (/^(?:[a-z][a-z0-9+.-]*:|#|\/\/)/iu.test(target)) continue;
    target = target.replace(/[#?].*$/u, "");
    if (!target || target.startsWith("/")) continue;
    try {
      target = decodeURIComponent(target);
    } catch {
      // Keep the raw target when it is not valid percent-encoding.
    }
    const resolved = path.resolve(fileDir, target);
    if (!isInside(rootDir, resolved)) {
      report.add("path-outside-root", link.line, "Link target `" + target + "` resolves outside the audited root and was not checked.");
    } else if (!lexists(resolved)) {
      report.add("broken-link", link.line, "Link target `" + target + "` does not exist.");
    }
  }

  const seenHeadings = new Map();
  for (const heading of scan.headings) {
    const key = heading.level + ":" + normalizeLine(heading.text);
    if (seenHeadings.has(key)) {
      report.add("duplicate-heading", heading.line, "Heading \"" + heading.text + "\" already appears on line " + seenHeadings.get(key) + "; merge the sections.");
    } else {
      seenHeadings.set(key, heading.line);
    }
  }

  for (const prose of scan.proseLines) {
    const lowered = prose.text.toLowerCase();
    const phrase = VAGUE_PHRASES.find((candidate) => lowered.includes(candidate));
    if (phrase) {
      report.add("vague-instruction", prose.line, "\"" + phrase + "\" is not actionable; name the specific command, convention, or check instead.");
    }
  }

  scan.lines.forEach((line, index) => {
    for (const [label, pattern] of SECRET_PATTERNS) {
      const match = line.match(pattern);
      if (match) {
        report.add("secret-like", index + 1, label + " detected (" + redact(match[0]) + "). Remove it and rotate the credential.");
      }
    }
  });

  return scan;
}

export function auditRepository(rootInput, options = {}) {
  const maxBytes = options.maxBytes ?? DEFAULT_MAX_BYTES;
  const maxFiles = options.maxFiles ?? DEFAULT_MAX_FILES;
  listingCache.clear();
  const rootDir = fs.realpathSync(path.resolve(rootInput));
  if (!fs.statSync(rootDir).isDirectory()) {
    throw new Error("root must be a directory");
  }
  listingRoot = rootDir;

  const findings = [];
  const { files, truncated } = discoverInstructionFiles(rootDir, maxFiles);
  const cache = { packages: new Map(), makefiles: new Map() };
  const context = new Context(rootDir, cache);
  const fileRecords = [];
  const byDirectory = new Map();

  for (const filePath of files) {
    const relative = toPosix(path.relative(rootDir, filePath));
    const bytes = fs.lstatSync(filePath).size;
    const text = readTextLimited(filePath);
    const fileFindings = [];
    const managersUsed = new Map();

    const report = {
      add(id, line, message, severityOverride) {
        fileFindings.push({ id, severity: severityOverride ?? RULES[id].severity, file: relative, line, message });
      },
      managerUse(manager, line, baseDir) {
        const key = manager + "\u0000" + baseDir;
        if (!managersUsed.has(key)) managersUsed.set(key, { manager, line, baseDir });
      },
    };

    const scan = checkFile(filePath, text, context, report);

    for (const use of managersUsed.values()) {
      const { managers, files: lockfiles } = detectLockManagers(use.baseDir, rootDir);
      if (managers.size === 1 && !managers.has(use.manager)) {
        report.add(
          "package-manager-mismatch",
          use.line,
          "Uses " + use.manager + " but the repository lockfile is " + lockfiles.join(", ") + " (" + [...managers][0] + ").",
        );
      }
    }

    if (bytes > maxBytes) {
      report.add("oversize-file", 1, "File is " + bytes + " bytes, above the " + maxBytes + "-byte budget; content past the budget may not reach the agent.");
    }

    const record = { path: relative, absolute: filePath, bytes, scan, findings: fileFindings };
    fileRecords.push(record);
    const directory = path.dirname(filePath);
    if (!byDirectory.has(directory)) byDirectory.set(directory, {});
    byDirectory.get(directory)[path.basename(filePath)] = record;
  }

  // Effective file per directory: an override replaces AGENTS.md in that directory.
  const effective = new Map();
  for (const [directory, entries] of byDirectory) {
    const override = entries["AGENTS.override.md"];
    const base = entries["AGENTS.md"];
    if (override && base) {
      override.findings.push({
        id: "override-shadows",
        severity: RULES["override-shadows"].severity,
        file: override.path,
        line: 1,
        message: "This override is used instead of " + base.path + "; edits to " + base.path + " have no effect in this directory.",
      });
    }
    effective.set(directory, override ?? base);
  }

  function ancestorsOf(directory) {
    const chain = [];
    let current = path.dirname(directory);
    while (isInside(rootDir, current) && current !== directory) {
      if (effective.has(current)) chain.unshift(effective.get(current));
      if (current === rootDir) break;
      directory = current;
      current = path.dirname(current);
    }
    return chain;
  }

  for (const [directory, record] of effective) {
    const ancestors = ancestorsOf(directory);
    const parentBytes = ancestors.reduce((sum, item) => sum + item.bytes, 0);
    const chainBytes = parentBytes + record.bytes;
    if (record.bytes <= maxBytes && chainBytes > maxBytes && parentBytes <= maxBytes) {
      record.findings.push({
        id: "oversize-chain",
        severity: RULES["oversize-chain"].severity,
        file: record.path,
        line: 1,
        message:
          "Root-to-here instructions total " + chainBytes + " bytes (" + [...ancestors, record].map((item) => item.path).join(" + ") + "), above the " + maxBytes + "-byte budget.",
      });
    }

    const parentLines = new Map();
    for (const ancestor of ancestors) {
      for (const prose of ancestor.scan.proseLines) {
        const normalized = normalizeLine(prose.text);
        if (normalized.length >= 30 && !normalized.startsWith("#") && !parentLines.has(normalized)) {
          parentLines.set(normalized, ancestor.path + ":" + prose.line);
        }
      }
    }
    for (const prose of record.scan.proseLines) {
      const normalized = normalizeLine(prose.text);
      if (parentLines.has(normalized)) {
        record.findings.push({
          id: "repeated-from-parent",
          severity: RULES["repeated-from-parent"].severity,
          file: record.path,
          line: prose.line,
          message: "Already stated in " + parentLines.get(normalized) + "; nested files inherit parent instructions.",
        });
      }
    }
  }

  for (const record of fileRecords) findings.push(...record.findings);

  if (files.length > 0 && !byDirectory.has(rootDir)) {
    findings.push({
      id: "missing-root",
      severity: RULES["missing-root"].severity,
      file: ".",
      line: 0,
      message: "No AGENTS.md at the repository root; shared project instructions have no home.",
    });
  }
  if (truncated) {
    findings.push({
      id: "discovery-limit",
      severity: RULES["discovery-limit"].severity,
      file: ".",
      line: 0,
      message: "Stopped after " + maxFiles + " instruction files; rerun with a narrower --root or a larger --max-files.",
    });
  }

  const seen = new Set();
  const unique = findings.filter((finding) => {
    const key = [finding.id, finding.file, finding.line, finding.message].join("\u0000");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  unique.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line || a.id.localeCompare(b.id));

  const summary = { files: fileRecords.length, error: 0, warning: 0, info: 0 };
  for (const finding of unique) summary[finding.severity] += 1;

  return {
    tool: "agents-md-auditor",
    version: TOOL_VERSION,
    readOnly: true,
    writesPerformed: false,
    root: ".",
    maxBytes,
    files: fileRecords.map((record) => ({
      path: record.path,
      bytes: record.bytes,
      effective: effective.get(path.dirname(record.absolute)) === record,
    })),
    findings: unique,
    summary,
    ok: summary.error === 0,
  };
}

export function formatText(result) {
  const lines = [];
  const { summary } = result;
  lines.push(
    "AGENTS.md audit: " + summary.files + " file" + (summary.files === 1 ? "" : "s") + ", " +
      summary.error + " error" + (summary.error === 1 ? "" : "s") + ", " +
      summary.warning + " warning" + (summary.warning === 1 ? "" : "s") + ", " +
      summary.info + " info (read-only; no files written)",
  );
  if (summary.files === 0) {
    lines.push("", "No AGENTS.md or AGENTS.override.md files found under the audited root.");
  }
  let currentFile = null;
  for (const finding of result.findings) {
    if (finding.file !== currentFile) {
      currentFile = finding.file;
      lines.push("", currentFile);
    }
    lines.push(
      "  " + String(finding.line).padStart(4) + "  " + finding.severity.padEnd(7) + "  " + finding.id.padEnd(24) + "  " + finding.message,
    );
  }
  return lines.join("\n") + "\n";
}

const USAGE = `Usage:
  agents-md-audit.mjs audit [--root DIR] [--format text|json] [--max-bytes N]
                            [--max-files N] [--fail-on error|warning|info|never]
  agents-md-audit.mjs rules [--format text|json]

Read-only. Exit codes: 0 = nothing at or above --fail-on (default error),
1 = findings at or above --fail-on, 2 = usage or input error.
`;

function parseArgs(argv) {
  const [command, ...rest] = argv;
  const options = { command, root: ".", format: "text", maxBytes: DEFAULT_MAX_BYTES, maxFiles: DEFAULT_MAX_FILES, failOn: "error" };
  for (let index = 0; index < rest.length; index += 1) {
    const flag = rest[index];
    const value = rest[index + 1];
    const needValue = () => {
      if (value === undefined || value.startsWith("--")) throw new Error(flag + " requires a value");
      index += 1;
      return value;
    };
    switch (flag) {
      case "--root":
        options.root = needValue();
        break;
      case "--format":
        options.format = needValue();
        if (!["text", "json"].includes(options.format)) throw new Error("--format must be text or json");
        break;
      case "--max-bytes":
        options.maxBytes = Number(needValue());
        if (!Number.isInteger(options.maxBytes) || options.maxBytes <= 0) throw new Error("--max-bytes must be a positive integer");
        break;
      case "--max-files":
        options.maxFiles = Number(needValue());
        if (!Number.isInteger(options.maxFiles) || options.maxFiles <= 0) throw new Error("--max-files must be a positive integer");
        break;
      case "--fail-on":
        options.failOn = needValue();
        if (!["error", "warning", "info", "never"].includes(options.failOn)) throw new Error("--fail-on must be error, warning, info, or never");
        break;
      case "-h":
      case "--help":
        options.command = "help";
        break;
      default:
        throw new Error("unknown argument " + flag);
    }
  }
  return options;
}

export function main(argv = process.argv.slice(2), io = { stdout: process.stdout, stderr: process.stderr }) {
  let options;
  try {
    options = parseArgs(argv);
  } catch (error) {
    io.stderr.write("error: " + error.message + "\n\n" + USAGE);
    return 2;
  }

  if (!options.command || options.command === "help" || options.command === "--help" || options.command === "-h") {
    io.stdout.write(USAGE);
    return options.command ? 0 : 2;
  }

  if (options.command === "rules") {
    if (options.format === "json") {
      io.stdout.write(JSON.stringify(RULES, null, 2) + "\n");
    } else {
      for (const [id, rule] of Object.entries(RULES)) {
        io.stdout.write(id.padEnd(26) + rule.severity.padEnd(9) + rule.summary + "\n");
      }
    }
    return 0;
  }

  if (options.command !== "audit") {
    io.stderr.write("error: unknown command " + options.command + "\n\n" + USAGE);
    return 2;
  }

  let result;
  try {
    result = auditRepository(options.root, { maxBytes: options.maxBytes, maxFiles: options.maxFiles });
  } catch (error) {
    const reason = error && error.code === "ENOENT" ? "root does not exist" : error.message;
    io.stderr.write("error: cannot audit root: " + reason + "\n");
    return 2;
  }

  io.stdout.write(options.format === "json" ? JSON.stringify(result, null, 2) + "\n" : formatText(result));

  if (options.failOn === "never") return 0;
  const threshold = SEVERITY_ORDER[options.failOn];
  return result.findings.some((finding) => SEVERITY_ORDER[finding.severity] >= threshold) ? 1 : 0;
}

const invokedDirectly = (() => {
  try {
    return process.argv[1] && fs.realpathSync(process.argv[1]) === fs.realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
})();

if (invokedDirectly) {
  process.exitCode = main();
}

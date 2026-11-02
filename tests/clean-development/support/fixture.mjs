import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const sourcePlugin = path.join(repository, "plugins", "clean-development");

export function pathListing(root) {
  const entries = [];
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const target = path.join(directory, entry.name);
      const relative = path.relative(root, target);
      entries.push(`${entry.isDirectory() ? "d" : entry.isFile() ? "f" : "o"}:${relative}`);
      if (entry.isDirectory()) visit(target);
    }
  };
  visit(root);
  return entries.sort();
}

export function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "community-clean-development-"));
  const home = path.join(root, "home");
  const project = path.join(root, "project");
  const managed = path.join(root, "managed");
  const fakeBin = path.join(root, "bin");
  const plugin = path.join(root, "installed-plugin", "clean-development");
  fs.mkdirSync(home, { recursive: true });
  fs.mkdirSync(project, { recursive: true });
  fs.mkdirSync(fakeBin, { recursive: true });
  fs.mkdirSync(path.dirname(plugin), { recursive: true });
  fs.cpSync(sourcePlugin, plugin, { recursive: true, dereference: false });
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return {
    root, project, managed, fakeBin, plugin, cli: path.join(plugin, "bin", "clean-development.js"),
    env: {
      HOME: home,
      CLEAN_DEVELOPMENT_HOME: home,
      CLEAN_DEVELOPMENT_DATA_HOME: path.join(root, "data"),
      CLEAN_DEVELOPMENT_CONFIG_HOME: path.join(root, "config"),
      CLEAN_DEVELOPMENT_ROOT: managed,
      PATH: [fakeBin, "/usr/bin", "/bin"].join(path.delimiter)
    }
  };
}

export function run(item, args, options = {}) {
  return spawnSync(process.execPath, [item.cli, ...args], {
    cwd: options.cwd || item.project,
    env: { ...item.env, ...(options.env || {}) },
    encoding: "utf8",
    timeout: 5_000
  });
}

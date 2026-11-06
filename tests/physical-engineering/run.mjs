#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const repo = path.resolve(fileURLToPath(new URL("../..", import.meta.url)));
const plugin = path.join(repo, "plugins/physical-engineering");
const suite = path.join(repo, "tests/physical-engineering");
const python = process.env.PE_PYTHON || "python3.14";
const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
const expectedSkills = [
  "engineer-subsystem",
  "prepare-engineering-handoff",
  "verify-engineering-design",
];
const setup = "Run python3.14 plugins/physical-engineering/scripts/engineering.py setup --runtime /absolute/path/to/runtime, then set PE_RUNTIME to that directory and PE_PYTHON to its bin/python.";

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}
function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolute = path.join(directory, entry.name);
    assert(!entry.isSymbolicLink(), `Package must not contain symlinks: ${absolute}`);
    assert(![".git", ".venv", "__pycache__", "node_modules", "recordings"].includes(entry.name),
      `Generated/runtime directory must not be installed: ${absolute}`);
    return entry.isDirectory() ? walk(absolute) : [absolute];
  });
}
function inside(file) {
  const relative = path.relative(plugin, file);
  return relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}
function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: repo, env, encoding: "utf8", timeout: 20 * 60 * 1000, ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} failed (${result.status ?? result.signal}):\n${result.stdout || ""}${result.stderr || ""}`);
  }
  return result;
}

try {
  const manifest = readJson(path.join(plugin, ".codex-plugin/plugin.json"));
  assert.equal(manifest.name, "physical-engineering");
  assert.match(manifest.version, /^\d+\.\d+\.\d+(?:-[\w.-]+)?$/);
  assert.equal(manifest.skills, "./skills/");
  assert.equal(manifest.license, "Apache-2.0");
  assert.deepEqual(manifest.interface.capabilities, ["Read", "Write"]);
  assert(manifest.interface.defaultPrompt.every((prompt) => prompt.length <= 128),
    "Codex ignores default prompts longer than 128 characters");
  const catalog = readJson(path.join(repo, ".agents/plugins/marketplace.json"));
  const entry = catalog.plugins.find((item) => item.name === manifest.name);
  assert(entry, "Missing catalog entry");
  assert.equal(entry.source.path, "./plugins/physical-engineering");
  assert.equal(entry.category, manifest.interface.category);
  assert.equal(readJson(path.join(repo, "package.json")).scripts["test:physical-engineering"],
    "node tests/physical-engineering/run.mjs");
  for (const required of [
    "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "assets/icon.svg",
    "scripts/engineering.py", "scripts/requirements.txt",
    "fixtures/drone-module/requirements.json",
    "references/brief-template.md", "references/compatibility.md", "references/session-guide.md",
  ]) assert(fs.statSync(path.join(plugin, required)).isFile(), `Missing ${required}`);
  const skills = fs.readdirSync(path.join(plugin, "skills")).sort();
  assert.deepEqual(skills, expectedSkills);
  for (const name of skills) {
    const skill = fs.readFileSync(path.join(plugin, "skills", name, "SKILL.md"), "utf8");
    assert.match(skill, /^---\nname: [a-z-]+\ndescription: .+\n---\n/);
    assert(skill.includes(`name: ${name}\n`));
    const agent = fs.readFileSync(path.join(plugin, "skills", name, "agents/openai.yaml"), "utf8");
    assert.match(agent, /^interface:\n/);
    for (const field of ["display_name", "short_description", "default_prompt"])
      assert(agent.includes(`  ${field}:`), `Missing ${field} in ${name}`);
  }
  const files = walk(plugin);
  for (const file of files) {
    assert(!/\.(?:mp4|mov|webm|pyc|pyo)$/i.test(file), `Generated media/bytecode in package: ${file}`);
    if (!/\.(?:md|json|yaml|py|txt|mjs|svg|sh)$/.test(file)) continue;
    const content = fs.readFileSync(file, "utf8");
    assert(!/\/Users\/|\/home\/[^/\s]+\//.test(content), `Personal path in package: ${file}`);
    assert(!/openai(?:-corpws|\.enterprise)?\.slack\.com|mail\.google\.com\/mail\//.test(content),
      `Private research link in package: ${file}`);
    if (file.endsWith(".md") && !file.endsWith("/README.md")) {
      for (const match of content.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
        const target = match[1].split("#")[0];
        if (!target || /^[a-z][a-z0-9+.-]*:/i.test(target)) continue;
        const resolved = path.resolve(path.dirname(file), target);
        assert(inside(resolved), `Runtime reference escapes plugin: ${file} -> ${target}`);
        assert(fs.existsSync(resolved), `Broken runtime reference: ${file} -> ${target}`);
      }
    }
  }
  console.log(`Physical Engineering package checks passed (${files.length} files, 3 skills).`);

  let probe;
  try {
    probe = run(python, ["-c", [
      "import sys, importlib.metadata as m",
      "assert sys.version_info >= (3,14), 'Python 3.14 or newer is required'",
      "assert m.version('cad') == m.version('cad-server') == '0.10.3', 'Text-to-CAD 0.10.3 is required'",
      "import build123d, OCP, cad.eng_drawing, pypdf",
      "print('Runtime:', sys.version.split()[0], 'cad', m.version('cad'), 'build123d', m.version('build123d'))",
    ].join("\n")]);
  } catch (error) {
    throw new Error(`${error.message}\n${setup}`);
  }
  process.stdout.write(probe.stdout);

  const cold = fs.mkdtempSync(path.join(os.tmpdir(), "physical-engineering-package-"));
  try {
    const copied = path.join(cold, "plugin");
    fs.cpSync(plugin, copied, { recursive: true });
    run(python, [path.join(copied, "scripts/engineering.py"), "--help"], { cwd: cold });
    run(python, ["-I", "-S", "-c",
      "import runpy, sys; sys.path.insert(0, sys.argv[1]); runpy.run_path(sys.argv[1] + '/engineering.py'); import cad_backend",
      path.join(copied, "scripts")], { cwd: cold });
    assert(!fs.existsSync(path.join(copied, "scripts/__pycache__")),
      "Normal wrapper imports must not write bytecode into the installed package");
    console.log("Cold copied package CLI startup passed.");
  } finally {
    fs.rmSync(cold, { recursive: true, force: true });
  }

  run(python, ["-c", [
    "import sys, unittest; from pathlib import Path",
    "root = Path(sys.argv[1])",
    "for test_file in root.rglob('test_*.py'):",
    "    parent = test_file.parent",
    "    while parent != root:",
    "        assert (parent / '__init__.py').is_file(), f'Test directory is not discoverable: {parent}'",
    "        parent = parent.parent",
    "suite = unittest.defaultTestLoader.discover(sys.argv[1], pattern='test_*.py')",
    "count = suite.countTestCases()",
    "assert count > 0, 'No Python tests discovered; add tests and package __init__.py files'",
    "print(f'Running {count} Physical Engineering tests', flush=True)",
    "result = unittest.TextTestRunner(verbosity=2).run(suite)",
    "sys.exit(0 if result.wasSuccessful() else 1)",
  ].join("\n"), suite], { stdio: "inherit" });
  console.log("Physical Engineering master suite passed.");
} catch (error) {
  console.error(`ERROR ${error.message}`);
  process.exit(1);
}

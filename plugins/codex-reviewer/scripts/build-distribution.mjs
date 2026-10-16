import { build } from "esbuild";
import { readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(fileURLToPath(new URL("../", import.meta.url)));
const manifest = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));

// 1. 服务端打成单文件，只保留 Node 内置模块依赖；兼容依赖中的 CommonJS require。
await build({
  absWorkingDir: root,
  entryPoints: ["src/server/index.ts"],
  outfile: "dist/server/index.js",
  bundle: true,
  platform: "node",
  target: "node22.12",
  format: "esm",
  banner: { js: 'import { createRequire } from "node:module"; const require = createRequire(import.meta.url);' },
  legalComments: "inline",
  logLevel: "info",
});

// 2. 收集运行依赖及其传递依赖的原始许可，不把 node_modules 带进发布仓库。
const packages = new Map();
async function collect(name, from, optional = false) {
  let directory = from;
  let packageRoot;
  let metadata;
  while (true) {
    packageRoot = path.join(directory, "node_modules", name);
    try {
      metadata = JSON.parse(await readFile(path.join(packageRoot, "package.json"), "utf8"));
      break;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    if (directory === root) {
      if (optional) return;
      throw new Error(`Missing runtime dependency: ${name}`);
    }
    const parent = path.dirname(directory);
    if (parent === directory) throw new Error(`Dependency is outside the project: ${name}`);
    directory = parent;
  }
  if (packages.has(packageRoot)) return;
  packages.set(packageRoot, { metadata, licenses: [] });
  const files = (await readdir(packageRoot, { withFileTypes: true }))
    .filter((entry) => entry.isFile() && /licen[cs]e|copying|notice|copyright/i.test(entry.name))
    .map((entry) => entry.name).sort();
  if (!files.length) {
    // 此版本的 npm 包遗漏许可文件，使用对应 gitHead 的上游原文。
    if (metadata.name !== "@cfworker/json-schema" || metadata.version !== "4.1.1") throw new Error(`Missing license text: ${metadata.name}`);
    packages.get(packageRoot).licenses.push(await readFile(path.join(root, "scripts/licenses/cfworker-json-schema-4.1.1.txt"), "utf8"));
  }
  for (const file of files) {
    packages.get(packageRoot).licenses.push(`${file}\n\n${await readFile(path.join(packageRoot, file), "utf8")}`);
  }
  const optionalNames = new Set(Object.keys(metadata.optionalDependencies ?? {}));
  for (const dependency of Object.keys({ ...metadata.dependencies, ...metadata.optionalDependencies }).sort()) {
    await collect(dependency, packageRoot, optionalNames.has(dependency));
  }
}
for (const name of Object.keys(manifest.dependencies).sort()) await collect(name, path.resolve(root));

const notices = [...packages.values()].sort((a, b) => a.metadata.name.localeCompare(b.metadata.name))
  .map(({ metadata, licenses }) => `## ${metadata.name}@${metadata.version}\nLicense: ${typeof metadata.license === "string" ? metadata.license : JSON.stringify(metadata.license)}\n\n${licenses.join("\n\n")}`);
notices.push(`## VS Code Seti\n\n${await readFile(path.join(root, "src/app/assets/seti/LICENSE.txt"), "utf8")}\n${await readFile(path.join(root, "src/app/assets/seti/ThirdPartyNotices.txt"), "utf8")}`);
await writeFile(path.join(root, "dist/THIRD_PARTY_NOTICES.txt"), `Reviewer bundled third-party notices\n\n${notices.join("\n\n---\n\n")}\n`);

#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { build } from "esbuild";
import { validateEvidence } from "./evidence.mjs";
import { buildOfflineHtml } from "./bundle_html.mjs";
const here = path.dirname(fileURLToPath(import.meta.url));
const args = {};
for (let i = 2; i < process.argv.length; i += 2)
  args[process.argv[i].slice(2)] = process.argv[i + 1];
if (!args.assets || !args.audio || !args.captions || !args.output)
  throw Error("Use --assets DIR --audio WAV --captions JSON --output HTML");
const { manifest, manifestSha256 } = validateEvidence(args.assets);
const data = {
  manifest,
  meshes: Object.fromEntries(
    Object.values(manifest.meshes).map((m) => [
      m.path,
      fs.readFileSync(path.join(args.assets, m.path)).toString("base64"),
    ]),
  ),
  audio: fs.readFileSync(args.audio).toString("base64"),
  captions: JSON.parse(fs.readFileSync(args.captions, "utf8")),
};
const bundle = await build({
  entryPoints: [path.join(here, "showcase.mjs")],
  bundle: true,
  minify: true,
  format: "esm",
  write: false,
  legalComments: "inline",
});
const html = buildOfflineHtml(
  fs.readFileSync(path.join(here, "index.html"), "utf8"),
  data,
  bundle.outputFiles[0].text,
);
const output = path.resolve(args.output);
fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, html);
console.log(
  JSON.stringify({
    file: output,
    bytes: Buffer.byteLength(html),
    sha256: createHash("sha256").update(html).digest("hex"),
    manifest_sha256: manifestSha256,
    offline: true,
  }),
);

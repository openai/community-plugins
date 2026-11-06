#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { validateEvidence } from "./evidence.mjs";
const here = path.dirname(fileURLToPath(import.meta.url));
const opts = { mode: "preview", fps: "24" };
for (let i = 2; i < process.argv.length; i += 2) {
  if (!process.argv[i].startsWith("--") || !process.argv[i + 1])
    throw Error("Use --name value arguments");
  opts[process.argv[i].slice(2)] = process.argv[i + 1];
}
for (const k of ["assets", "vendor", "output"])
  if (!opts[k]) throw Error(`Missing --${k}`);
const assets = path.resolve(opts.assets),
  vendor = path.resolve(opts.vendor),
  output = path.resolve(opts.output);
fs.mkdirSync(output, { recursive: true });
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");
const evidence = validateEvidence(assets);
const manifest = evidence.manifest;
const manifestBytes = fs.readFileSync(path.join(assets, "manifest.json"));
const mime = {
  ".html": "text/html",
  ".mjs": "text/javascript",
  ".js": "text/javascript",
  ".stl": "application/octet-stream",
  ".json": "application/json",
  ".wav": "audio/wav",
};
const server = http.createServer((req, res) => {
  try {
    let url = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    let root, rel;
    if (url.startsWith("/assets/")) {
      root = assets;
      rel = url.slice(8);
    } else if (url.startsWith("/vendor/")) {
      root = vendor;
      rel = url.slice(8);
    } else if (["/narration.wav", "/captions.json"].includes(url)) {
      root = path.dirname(assets);
      rel = url.slice(1);
    } else {
      root = here;
      rel = url === "/" ? "index.html" : url.slice(1);
    }
    const file = path.resolve(root, rel);
    if (
      !file.startsWith(root + path.sep) ||
      !fs.existsSync(file) ||
      !fs.statSync(file).isFile()
    ) {
      res.writeHead(404).end();
      return;
    }
    res.setHeader(
      "Content-Type",
      mime[path.extname(file)] || "application/octet-stream",
    );
    res.setHeader("Cache-Control", "no-store");
    fs.createReadStream(file).pipe(res);
  } catch {
    res.writeHead(400).end();
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const url = `http://127.0.0.1:${server.address().port}/`;
console.log(JSON.stringify({ url, mode: opts.mode }));
if (opts.mode === "serve") {
  process.on("SIGINT", () => server.close(() => process.exit()));
} else {
  const require = createRequire(import.meta.url);
  const { chromium } = opts["playwright-module"]
    ? require(opts["playwright-module"])
    : require("playwright");
  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      args:
        opts.angle === "metal"
          ? ["--enable-webgl", "--use-angle=metal"]
          : [
              "--enable-webgl",
              "--use-angle=swiftshader",
              "--enable-unsafe-swiftshader",
            ],
    });
    const page = await browser.newPage({
      viewport: { width: 1920, height: 1080 },
      deviceScaleFactor: 1,
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto(url);
    await page.waitForFunction(() => window.ready, { timeout: 30000 });
    await page.evaluate(() => document.body.classList.add("recording"));
    const duration = await page.evaluate(() => window.showcaseDuration),
      fps = Number(opts.fps);
    if (!Number.isFinite(fps) || fps < 1 || fps > 60)
      throw Error("Invalid fps");
    const previewTimes = [0.8, 6, 17, 30, 42, 56, 70, 76, 87, 101, 115];
    const states = [];
    for (const t of previewTimes) {
      const state = await page.evaluate((t) => window.seekShowcase(t), t);
      const name = `preview-${String(t).replace(".", "_")}.png`;
      await page.screenshot({ path: path.join(output, name) });
      states.push({ time: t, file: name, state });
    }
    fs.writeFileSync(
      path.join(output, "preview-index.json"),
      JSON.stringify(states, null, 2) + "\n",
    );
    if (errors.length) throw Error(errors.join("\n"));
    if (opts.mode === "video") {
      const movie = path.join(output, "showcase-silent.mp4");
      const proc = spawn(
        "ffmpeg",
        [
          "-nostdin",
          "-hide_banner",
          "-loglevel",
          "error",
          "-y",
          "-f",
          "image2pipe",
          "-vcodec",
          "mjpeg",
          "-framerate",
          String(fps),
          "-i",
          "pipe:0",
          "-an",
          "-c:v",
          "libx264",
          "-preset",
          "fast",
          "-crf",
          "18",
          "-pix_fmt",
          "yuv420p",
          "-movflags",
          "+faststart",
          movie,
        ],
        { stdio: ["pipe", "inherit", "inherit"] },
      );
      const done = new Promise((resolve, reject) => {
        proc.on("error", reject);
        proc.on("close", (code) =>
          code === 0 ? resolve() : reject(Error(`ffmpeg exit ${code}`)),
        );
      });
      let nextReport = 0;
      for (let i = 0; i < duration * fps; i++) {
        await page.evaluate((t) => window.seekShowcase(t), i / fps);
        const jpeg = await page.screenshot({ type: "jpeg", quality: 95 });
        if (!proc.stdin.write(jpeg))
          await new Promise((r) => proc.stdin.once("drain", r));
        if (i >= nextReport) {
          console.log(
            JSON.stringify({
              frame: i,
              of: duration * fps,
              video_seconds: i / fps,
            }),
          );
          nextReport += fps * 10;
        }
      }
      proc.stdin.end();
      await done;
      if (errors.length) throw Error(errors.join("\n"));
      fs.writeFileSync(
        path.join(output, "render-receipt.json"),
        JSON.stringify(
          {
            schema: 1,
            kind: "Edited animated explanation of verified CAD; not a new CLI recording",
            duration_seconds: duration,
            fps,
            width: 1920,
            height: 1080,
            manifest_sha256: sha(manifestBytes),
            video_sha256: sha(fs.readFileSync(movie)),
            source_sha256: Object.fromEntries(
              ["index.html", "showcase.mjs", "render.mjs"].map((f) => [
                f,
                sha(fs.readFileSync(path.join(here, f))),
              ]),
            ),
            errors,
            preview_samples: states,
          },
          null,
          2,
        ) + "\n",
      );
      console.log(JSON.stringify({ complete: true, movie }));
    }
  } finally {
    await browser?.close();
    server.close();
  }
}

import fs from "node:fs";
import path from "node:path";
import http from "node:http";

const mime = {
  ".html": "text/html",
  ".mjs": "text/javascript",
  ".js": "text/javascript",
  ".stl": "application/octet-stream",
  ".json": "application/json",
  ".wav": "audio/wav",
};
const playerFiles = new Set(["index.html", "showcase.mjs", "captions.mjs"]);

function readFile(root, relative) {
  if (typeof relative !== "string" || /[\\\0:]/.test(relative)) return null;
  const parts = relative.split("/");
  if (parts.some((part) => !part || part === "." || part === "..")) return null;
  let file = root;
  for (const [index, part] of parts.entries()) {
    file = path.join(file, part);
    const stat = fs.lstatSync(file);
    if (stat.isSymbolicLink() ||
        !(index === parts.length - 1 ? stat.isFile() : stat.isDirectory())) return null;
  }
  const canonical = fs.realpathSync(file);
  if (!canonical.startsWith(root + path.sep)) return null;
  // Read the checked descriptor, not a second pathname opened by a stream.
  const fd = fs.openSync(canonical, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    return fs.fstatSync(fd).isFile() ? fs.readFileSync(fd) : null;
  } finally {
    fs.closeSync(fd);
  }
}

export function createPreviewServer({ assets, vendor, player, assetPaths }) {
  // Explicit roots may use normal filesystem aliases (for example /tmp).
  const roots = Object.fromEntries(Object.entries({
    assets, vendor, player, narration: path.dirname(assets),
  }).map(([name, root]) => [name, fs.realpathSync(root)]));
  const allowedAssets = new Set(["manifest.json", "construction-manifest.json", ...assetPaths]);
  return http.createServer((req, res) => {
    let url;
    try {
      url = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    } catch {
      res.writeHead(400).end();
      return;
    }
    let root, relative;
    if (url.startsWith("/assets/")) {
      root = roots.assets;
      relative = url.slice(8);
      if (!allowedAssets.has(relative)) return res.writeHead(404).end();
    } else if (url.startsWith("/vendor/")) {
      root = roots.vendor;
      relative = url.slice(8);
    } else if (["/narration.wav", "/captions.json"].includes(url)) {
      root = roots.narration;
      relative = url.slice(1);
    } else {
      root = roots.player;
      relative = url === "/" ? "index.html" : url.slice(1);
      if (!playerFiles.has(relative)) return res.writeHead(404).end();
    }
    try {
      const bytes = readFile(root, relative);
      if (bytes === null) return res.writeHead(404).end();
      res.writeHead(200, {
        "Content-Type": mime[path.extname(relative)] || "application/octet-stream",
        "Cache-Control": "no-store",
      }).end(bytes);
    } catch {
      res.writeHead(404).end();
    }
  });
}

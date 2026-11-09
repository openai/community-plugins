import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { renderCaption } from "../support/showcase/captions.mjs";
import { createPreviewServer } from "../support/showcase/preview_server.mjs";

test("captions preserve literal text and the styled span without parsing HTML", () => {
  const container = {
    children: [],
    ownerDocument: { createElement: (tag) => ({ tag, textContent: "" }) },
    replaceChildren(...children) { this.children = children; },
    set innerHTML(_) { throw new Error("Caption data reached the HTML parser"); },
  };
  for (const text of ["35 → 40 mm", '<img src=x onerror="globalThis.injected=true">',
    '</SCRIPT><script>alert(1)</script>', 'Quotes " & < > and café']) {
    renderCaption(container, { text });
    assert.deepEqual(container.children, [{ tag: "span", textContent: text }]);
  }
  renderCaption(container, undefined);
  assert.deepEqual(container.children, []);
  assert.throws(() => renderCaption(container, { text: {} }), /must be a string/);
});

async function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "preview-paths-"));
  const roots = Object.fromEntries(["assets", "vendor", "player"].map((name) => {
    const root = path.join(directory, name);
    fs.mkdirSync(root);
    return [name, root];
  }));
  fs.mkdirSync(path.join(roots.vendor, "examples/jsm"), { recursive: true });
  fs.mkdirSync(path.join(roots.vendor, "build"));
  for (const [name, bytes] of [
    ["assets/manifest.json", "{}"], ["assets/part.stl", "mesh"],
    ["vendor/build/three.module.js", "export {};"], ["vendor/examples/jsm/helper.js", "module"],
    ["player/index.html", "player"], ["player/showcase.mjs", "script"],
    ["player/captions.mjs", "caption module"], ["captions.json", "[]"], ["narration.wav", "audio"],
  ]) fs.writeFileSync(path.join(directory, name), bytes);
  const sentinel = path.join(directory, "outside.txt");
  fs.writeFileSync(sentinel, "outside sentinel");
  const server = createPreviewServer({ ...roots, assetPaths: ["part.stl", "nested/part.stl"] });
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const request = (url) => new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port: server.address().port, path: url }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString() }));
    }).on("error", reject);
  });
  return { directory, roots, sentinel, request };
}

test("preview serves player, validated assets, nested modules and optional narration", async (t) => {
  const { request } = await fixture(t);
  for (const [url, body] of [["/", "player"], ["/showcase.mjs", "script"],
    ["/captions.mjs", "caption module"], ["/assets/part.stl", "mesh"],
    ["/vendor/examples/jsm/helper.js", "module"], ["/vendor/build/three.module.js", "export {};"],
    ["/captions.json", "[]"], ["/narration.wav", "audio"]]) {
    assert.deepEqual(await request(url), { status: 200, body });
  }
});

test("preview rejects symlinked files on every route, including swapped declared files", async (t) => {
  const { directory, sentinel, request } = await fixture(t);
  for (const [file, url] of [["assets/part.stl", "/assets/part.stl"],
    ["vendor/build/three.module.js", "/vendor/build/three.module.js"],
    ["player/showcase.mjs", "/showcase.mjs"], ["captions.json", "/captions.json"],
    ["narration.wav", "/narration.wav"]]) {
    fs.unlinkSync(path.join(directory, file));
    fs.symlinkSync(sentinel, path.join(directory, file));
    const result = await request(url);
    assert.equal(result.status, 404, url);
    assert(!result.body.includes("outside sentinel"));
  }
});

test("preview rejects undeclared assets and symlinked parent directories", async (t) => {
  const { directory, roots, sentinel, request } = await fixture(t);
  fs.symlinkSync(sentinel, path.join(roots.assets, "unexpected.txt"));
  fs.symlinkSync(directory, path.join(roots.assets, "nested"), "dir");
  fs.symlinkSync(directory, path.join(roots.vendor, "linked"), "dir");
  for (const url of ["/assets/unexpected.txt", "/assets/nested/part.stl", "/vendor/linked/outside.txt", "/preview_server.mjs"])
    assert.equal((await request(url)).status, 404, url);
});

test("preview refuses alternate path forms and malformed encoding", async (t) => {
  const { request } = await fixture(t);
  for (const url of ["/vendor/%2e%2e%2foutside.txt", "/assets/%2e%2e%2foutside.txt",
    "/vendor/%5c..%5coutside.txt", "/vendor//build/three.module.js", "/vendor/C%3a/outside.txt",
    "/vendor/build/three.module.js%00", "/vendor/examples", "/assets-mirror/outside.txt"])
    assert.equal((await request(url)).status, 404, url);
  assert.equal((await request("/vendor/%zz")).status, 400);
});

test("missing optional audio and captions return not found", async (t) => {
  const { directory, request } = await fixture(t);
  for (const name of ["captions.json", "narration.wav"]) {
    fs.unlinkSync(path.join(directory, name));
    assert.equal((await request("/" + name)).status, 404);
  }
});

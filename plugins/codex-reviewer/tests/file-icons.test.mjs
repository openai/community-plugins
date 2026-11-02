import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { renderToStaticMarkup } from "react-dom/server";
import { FileIcon, resolveFileIcon } from "../src/app/FileIcon.tsx";

test("uses the official Seti language icons for common source files", () => {
  for (const [file, icon] of [["src/app.ts", "_typescript"], ["App.tsx", "_react"], ["config.json", "_json"], ["main.py", "_python"], ["Dockerfile", "_docker"]]) {
    assert.equal(resolveFileIcon(file).id, icon, file);
  }
});

test("renders official Seti paths without requiring a custom font", () => {
  for (const file of ["app.ts", "App.tsx", "config.json", "README.md", "file.unknown-extension"]) {
    const markup = renderToStaticMarkup(FileIcon({ path: file }));
    assert.match(markup, /<svg\b/);
    assert.match(markup, /<path d="M[^\"]+"/);
    assert.doesNotMatch(markup, /[\uE000-\uF8FF]/);
  }
});

test("file icons do not depend on a font-face declaration in the host", async () => {
  const styles = await readFile(new URL("../src/app/styles.css", import.meta.url), "utf8");
  assert.doesNotMatch(styles, /CodexSeti|seti\.woff/);
});

test("every official Seti icon has a nonempty vector outline", async () => {
  const readJson = async (name) => JSON.parse(await readFile(new URL(`../src/app/assets/seti/${name}`, import.meta.url), "utf8"));
  const theme = await readJson("vs-seti-icon-theme.json");
  const paths = await readJson("glyphs.json");
  for (const [id, icon] of Object.entries(theme.iconDefinitions)) {
    const outline = paths[icon.fontCharacter.slice(1)];
    assert.ok(outline, id);
    assert.match(outline.path, /^M/, id);
    const box = outline.viewBox.split(" ").map(Number);
    assert.ok(box.every(Number.isFinite) && box[2] > 0 && box[2] === box[3], id);
  }
});

test("honors Seti exact filenames before extension and language fallbacks", () => {
  assert.equal(resolveFileIcon("src/TSCONFIG.JSON").id, "_tsconfig");
  assert.equal(resolveFileIcon("README.md").id, "_info");
  assert.equal(resolveFileIcon("file.unknown-extension").id, "_default");
  assert.match(resolveFileIcon("app.ts").path, /^M/);
  assert.match(resolveFileIcon("app.ts").darkColor, /^#[\da-f]{6}$/i);
  assert.match(resolveFileIcon("file.unknown-extension").lightColor, /^#[\da-f]{6}$/i);
});

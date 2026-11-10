import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { basename, join } from "node:path";
import test from "node:test";

import { sha256 } from "../../scripts/validate-grafana.mjs";
import { REPOSITORY_ROOT } from "./helpers/mcp-client.mjs";

const mediaRoot = join(REPOSITORY_ROOT, "docs/media");
const manifest = JSON.parse(readFileSync(join(mediaRoot, "grafana-demo.json"), "utf8"));
const incidentManifest = JSON.parse(readFileSync(join(mediaRoot, "grafana-incident-workflows.json"), "utf8"));

function boxes(buffer, start = 0, end = buffer.length) {
  const result = [];
  for (let offset = start; offset < end;) {
    assert.ok(offset + 8 <= end, "Truncated MP4 box");
    let size = buffer.readUInt32BE(offset);
    const type = buffer.toString("ascii", offset + 4, offset + 8);
    let header = 8;
    if (size === 1) {
      assert.ok(offset + 16 <= end);
      size = Number(buffer.readBigUInt64BE(offset + 8));
      header = 16;
    } else if (size === 0) size = end - offset;
    assert.ok(Number.isSafeInteger(size) && size >= header && offset + size <= end, `Invalid MP4 box: ${type}`);
    result.push({ type, offset, data: offset + header, end: offset + size });
    offset += size;
  }
  return result;
}

function duration(buffer, movieHeader) {
  const version = buffer[movieHeader.data];
  assert.ok(version === 0 || version === 1, "Unsupported MP4 movie header");
  const scaleOffset = movieHeader.data + (version === 1 ? 20 : 12);
  const timescale = buffer.readUInt32BE(scaleOffset);
  const ticks = version === 1
    ? Number(buffer.readBigUInt64BE(scaleOffset + 4))
    : buffer.readUInt32BE(scaleOffset + 4);
  assert.ok(timescale > 0);
  return ticks / timescale;
}

function verifiedAsset(entry, checkBytes = true) {
  assert.equal(entry.filename, basename(entry.filename), "Media manifest path escapes its directory");
  const bytes = readFileSync(join(mediaRoot, entry.filename));
  if (checkBytes) assert.equal(bytes.byteLength, entry.bytes, entry.filename);
  assert.equal(sha256(bytes), entry.sha256, entry.filename);
  return bytes;
}

function verifiedVideo(entry) {
  const bytes = verifiedAsset(entry);
  const top = boxes(bytes);
  const moov = top.find((box) => box.type === "moov");
  const mdat = top.find((box) => box.type === "mdat");
  assert.ok(moov && mdat && moov.offset < mdat.offset, "MP4 is not fast-start enabled");
  const mvhd = boxes(bytes, moov.data, moov.end).find((box) => box.type === "mvhd");
  assert.ok(mvhd);
  const seconds = duration(bytes, mvhd);
  assert.ok(Math.abs(seconds - entry.durationSeconds) < 0.002, entry.filename);
  assert.ok(bytes.subarray(moov.data, moov.end).includes(Buffer.from("avc1")), "Expected browser-friendly H.264 video");
  return seconds;
}

function verifiedPoster(entry, width, height) {
  const png = verifiedAsset(entry);
  assert.equal(png.subarray(0, 8).toString("hex"), "89504e470d0a1a0a");
  assert.equal(png.readUInt32BE(16), width);
  assert.equal(png.readUInt32BE(20), height);
}

test("the media archive retains the verified fast-start 8× demo and full walkthrough", () => {
  assert.equal(manifest.schemaVersion, 1);
  assert.equal(manifest.primary.speed, 8);
  assert.equal(manifest.full.speed, 1);
  for (const entry of [manifest.primary, manifest.full]) {
    const seconds = verifiedVideo(entry);
    assert.ok(Math.abs(seconds - manifest.source.durationSeconds / entry.speed) < 0.04, "Playback speed changed");
  }
  verifiedPoster(manifest.poster, 1680, 956);
  const archiveReadme = readFileSync(join(mediaRoot, "README.md"), "utf8");
  for (const entry of [manifest.primary, manifest.full, manifest.poster]) {
    assert.ok(archiveReadme.includes(`](${entry.filename})`), `Media archive must link to ${entry.filename}`);
  }
});

test("the plugin guide features the verified incident recording and setup guides", () => {
  assert.equal(incidentManifest.schemaVersion, 1);
  const { edited, full, poster } = incidentManifest.outputs;
  for (const entry of [edited, full]) {
    assert.equal(entry.codec, "h264");
    assert.equal(entry.width, 1920);
    assert.equal(entry.height, 1080);
    verifiedVideo(entry);
  }
  assert.ok(Math.abs(full.durationSeconds - incidentManifest.source.durationSeconds) < 0.04, "Full recording duration changed");
  assert.ok(edited.durationSeconds < full.durationSeconds, "Edited walkthrough must be shorter than the full recording");
  verifiedPoster(poster, 1920, 1080);
  for (const entry of [...incidentManifest.drafts, incidentManifest.evidence]) verifiedAsset(entry, false);

  const pluginGuide = "plugins/grafana-observability/README.md";
  const readme = readFileSync(join(REPOSITORY_ROOT, pluginGuide), "utf8");
  const videoPosition = readme.indexOf(`](../../docs/media/${edited.filename})`);
  assert.ok(videoPosition >= 0 && videoPosition < readme.indexOf("## What it does"), "Incident video is not near the top of the plugin guide");
  for (const target of [
    `../../docs/media/${full.filename}`,
    `../../docs/media/${poster.filename}`,
    "../../docs/grafana-incident-video.md",
    "../../docs/grafana-synthetic-onboarding.md#quick-start",
    "#install",
    "#connect-a-real-grafana-instance",
  ]) assert.ok(readme.includes(`](${target})`), `Plugin guide must link to ${target}`);
  const marketplaceReadme = readFileSync(join(REPOSITORY_ROOT, "README.md"), "utf8");
  assert.ok(marketplaceReadme.includes(`](${pluginGuide})`), "Marketplace README must link to the plugin guide");
  assert.ok(marketplaceReadme.includes("](docs/grafana-incident-video.md)"), "Marketplace README must link to the incident guide");
});

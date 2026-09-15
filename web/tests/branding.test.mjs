import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = path => readFileSync(new URL(path, import.meta.url));
const publicFile = path => read(`../public${path}`);
function pngSize(bytes) {
  assert.equal(bytes.subarray(0, 8).toString("hex"), "89504e470d0a1a0a");
  assert.equal(bytes[25], 2, "Brand exports must be opaque RGB, without alpha");
  return `${bytes.readUInt32BE(16)}x${bytes.readUInt32BE(20)}`;
}

test("installable beta has correct name, entrypoint and actual icon dimensions", () => {
  const manifest = JSON.parse(publicFile("/manifest.webmanifest"));
  assert.equal(manifest.name, "The Job Pursuit");
  assert.equal(manifest.start_url, "/beta");
  assert.equal(manifest.background_color, "#F4F0E8");
  for (const icon of manifest.icons) assert.equal(pngSize(publicFile(icon.src)), icon.sizes);
});

test("browser, touch and email icons are deployable opaque PNG assets", () => {
  for (const [name, size] of [["favicon", 48], ["touch", 180], ["email", 96], ["mark", 256]]) {
    assert.equal(pngSize(publicFile(`/brand/unfold-${name}-v1.png`)), `${size}x${size}`);
  }
  const html = read("../index.html").toString();
  assert.ok(html.includes("<title>The Job Pursuit</title>"));
  assert.ok(html.includes("/brand/unfold-favicon-v1.png"));
  assert.ok(html.includes("/brand/unfold-touch-v1.png"));
  assert.ok(!html.includes("maximum-scale=1"), "Do not disable text zoom");
});

test("native icon meets the 1024px requirement and uses the same in-app artwork", () => {
  const assets = "../../ios/JobPursuit/Resources/Assets.xcassets/";
  const metadata = JSON.parse(read(assets + "AppIcon.appiconset/Contents.json"));
  assert.equal(pngSize(read(assets + "AppIcon.appiconset/" + metadata.images[0].filename)), "1024x1024");
  assert.deepEqual(read(assets + "UnfoldMark.imageset/unfold-mark.png"), publicFile("/brand/unfold-mark-v1.png"));
});

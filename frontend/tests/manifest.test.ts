/**
 * What a browser is told when somebody installs VocalLens.
 *
 * A manifest is a set of promises a platform renders without asking again:
 * the launcher icon, the splash screen, the window it opens in. The tests here
 * are about the promises being true and complete rather than about the file
 * parsing — a manifest with a missing maskable icon or a splash colour that
 * disagrees with the icon is valid and wrong.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import manifest from "../app/manifest.ts";

const built = manifest();

test("the app installs under its own name, in its own window", () => {
  assert.equal(built.name, "VocalLens");
  assert.equal(built.short_name, "VocalLens");
  // Not "fullscreen": this is an app somebody records into, and hiding the
  // clock and the battery while a take runs is the wrong trade.
  assert.equal(built.display, "standalone");
});

test("the app opens at the page it has", () => {
  assert.equal(built.start_url, "/");
  // The whole product is one page; a narrower scope would put its own links
  // outside the installed window.
  assert.equal(built.scope, "/");
});

test("the splash screen continues the icon rather than flashing against it", () => {
  // A platform paints the splash from these two values. The icon's own
  // background is the dark surface, so anything else here is a visible seam.
  assert.equal(built.background_color, "#0b0b0f");
  assert.equal(built.theme_color, "#0b0b0f");
});

test("there is a maskable icon, and it is not the ordinary one", () => {
  const icons = built.icons ?? [];
  const maskable = icons.filter((icon) => icon.purpose === "maskable");
  const any = icons.filter((icon) => icon.purpose === "any");

  assert.equal(maskable.length, 1);
  assert.ok(any.length >= 1);
  // Listing the same file as both is the common mistake: a maskable slot may be
  // cropped to a circle, and an icon drawn for a square loses its corners.
  assert.notEqual(maskable[0]?.src, any.find((icon) => icon.sizes === "512x512")?.src);
});

test("both sizes a launcher asks for are present", () => {
  const sizes = (built.icons ?? [])
    .filter((icon) => icon.purpose === "any")
    .map((icon) => icon.sizes);
  assert.ok(sizes.includes("192x192"));
  assert.ok(sizes.includes("512x512"));
});

test("every icon the manifest promises is a file that exists", async () => {
  const { access } = await import("node:fs/promises");
  for (const icon of built.icons ?? []) {
    const path = new URL(`../public${icon.src}`, import.meta.url);
    await assert.doesNotReject(access(path), `${icon.src} is promised and missing`);
  }
});

test("the manifest promises nothing the product does not have", () => {
  // Screenshots, shortcuts, a share target and protocol handlers are each
  // rendered by the platform, and an invented one is worse than its absence.
  for (const field of ["screenshots", "shortcuts", "share_target", "protocol_handlers"]) {
    assert.ok(!(field in built), `${field} is promised without being built`);
  }
});

test("the description says what the product measures", () => {
  const description = built.description ?? "";
  assert.ok(description.length > 40);
  assert.match(description, /pitch/i);
});

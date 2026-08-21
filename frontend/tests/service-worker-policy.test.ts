/**
 * What the service worker may cache.
 *
 * Two of the three rules under test are refusals, and they are the ones worth
 * having tests for:
 *
 * - **an API response is never cached**, because a cached measurement is
 *   presented as current and the client cannot tell that it is not — which is
 *   the one thing this product refuses to do anywhere else;
 * - **a document is never cached**, because a cached shell would serve one fixed
 *   CSP nonce for as long as it lived, and `app/layout.tsx` gave up the
 *   full-route cache precisely to keep that nonce per-request. (Not because a
 *   stale nonce breaks the page — measured, it does not: a cache stores the
 *   header alongside the body and the two stay in agreement.)
 *
 * The third is that content-hashed URLs may be served without asking, which is
 * where an installed app's speed comes from.
 *
 * These assert the *specification*. That the shipped `public/sw.js` behaves the
 * same way is established by driving a real Chromium — a unit test cannot
 * install a worker.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  API_PREFIX,
  IMMUTABLE_PREFIX,
  OFFLINE_URL,
  REVALIDATED_PATHS,
  cacheStrategyFor,
  isCacheable,
} from "../lib/service-worker-policy.ts";

// --- The refusals -----------------------------------------------------------

test("no API path is ever cached, whatever it looks like", () => {
  for (const path of [
    "/api/v1/recordings",
    "/api/v1/recordings/abc/audio-analysis",
    "/api/v1/recordings/abc/compatibility",
    "/api/v1/identity",
    "/api/v1/config",
    "/api/v1/health",
  ]) {
    assert.equal(cacheStrategyFor(path, "empty"), "never", path);
  }
});

test("an API path that looks like a static asset is still never cached", () => {
  // The API rule runs before the asset rules for exactly this reason. Were it
  // second, a path shaped like one of them would slip past it.
  assert.equal(cacheStrategyFor("/api/v1/_next/static/x.js", "script"), "never");
  assert.equal(cacheStrategyFor("/api/v1/icons/thing.png", "image"), "never");
});

test("an API request is refused the cache even when it is a document", () => {
  assert.equal(cacheStrategyFor("/api/v1/recordings", "document"), "never");
});

test("a document is never served from the cache", () => {
  // For what caching does to the nonce, not for correctness: a cached shell
  // would serve one nonce durably where the design wants a fresh one per
  // request.
  for (const path of ["/", "/anything", "/deep/path"]) {
    assert.equal(cacheStrategyFor(path, "document"), "offline-fallback", path);
  }
});

test("anything nobody named is served from the network and not written down", () => {
  // A default of "cache it" is how a cache comes to hold something nobody
  // decided it should hold.
  assert.equal(cacheStrategyFor("/some/new/route", "fetch"), "never");
  assert.equal(cacheStrategyFor("/favicon.ico", "image"), "never");
  assert.equal(cacheStrategyFor("/manifest.webmanifest", "manifest"), "never");
});

// --- What is cached ---------------------------------------------------------

test("content-hashed build output is served without asking", () => {
  assert.equal(cacheStrategyFor("/_next/static/chunks/main-abc123.js", "script"), "cache-first");
  assert.equal(cacheStrategyFor("/_next/static/css/app-def456.css", "style"), "cache-first");
});

test("a stable URL over changeable bytes is revalidated rather than trusted", () => {
  assert.equal(cacheStrategyFor("/icons/icon-192.png", "image"), "stale-while-revalidate");
  assert.equal(
    cacheStrategyFor("/pcm-capture-worklet.js", "audioworklet"),
    "stale-while-revalidate",
  );
});

test("the two kinds of asset are told apart by whether the URL carries a hash", () => {
  // The distinction is not "build output" versus "public file" — it is whether
  // the URL changes when the content does, which is what makes serving one
  // without checking safe and the other not.
  assert.notEqual(
    cacheStrategyFor(`${IMMUTABLE_PREFIX}x.js`, "script"),
    cacheStrategyFor(REVALIDATED_PATHS[0] + "x.png", "image"),
  );
});

// --- What may be stored -----------------------------------------------------

test("a failed response is never stored", () => {
  // Caching a 404 or a 502 turns a transient failure into a durable one, and
  // the only way back out is for the user to clear site data.
  assert.equal(isCacheable(404, "basic"), false);
  assert.equal(isCacheable(500, "basic"), false);
  assert.equal(isCacheable(503, "basic"), false);
  assert.equal(isCacheable(302, "basic"), false);
});

test("a successful same-origin response is stored", () => {
  assert.equal(isCacheable(200, "basic"), true);
  assert.equal(isCacheable(206, "basic"), true);
});

test("an opaque response is refused, because its status cannot be read", () => {
  assert.equal(isCacheable(0, "opaque"), false);
});

// --- The constants the worker mirrors ---------------------------------------

test("the prefixes are the ones the application actually uses", () => {
  // These are restated in public/sw.js. If one moves here and not there the
  // browser check catches it; these assertions make the intended values plain.
  assert.equal(API_PREFIX, "/api/");
  assert.equal(IMMUTABLE_PREFIX, "/_next/static/");
  assert.equal(OFFLINE_URL, "/offline.html");
});

// --- The worker is held to the specification --------------------------------

test("the shipped worker restates the same constants", async () => {
  // `public/sw.js` is a classic script with no bundler in front of it, so it
  // cannot import the module above and carries its own copy of these values.
  // The copies are checked rather than trusted: the browser check proves the
  // worker *behaves*, and this proves it was not quietly edited apart from the
  // specification between browser runs.
  const { readFile } = await import("node:fs/promises");
  const worker = await readFile(new URL("../public/sw.js", import.meta.url), "utf8");

  const declares = (name: string, value: string) =>
    assert.ok(
      worker.includes(`const ${name} = "${value}"`),
      `sw.js does not declare ${name} as "${value}"`,
    );

  declares("API_PREFIX", API_PREFIX);
  declares("IMMUTABLE_PREFIX", IMMUTABLE_PREFIX);
  declares("OFFLINE_URL", OFFLINE_URL);
  for (const path of REVALIDATED_PATHS) {
    assert.ok(worker.includes(`"${path}"`), `sw.js does not mention ${path}`);
  }
});

test("the worker checks the API prefix before anything else can claim a request", () => {
  // Order is load-bearing here, not stylistic. This asserts it in the
  // specification; the assertion on the shipped file is below.
  assert.equal(cacheStrategyFor("/api/v1/_next/static/x.js", "script"), "never");
});

test("the shipped worker refuses API paths before it looks at anything else", async () => {
  const { readFile } = await import("node:fs/promises");
  const worker = await readFile(new URL("../public/sw.js", import.meta.url), "utf8");
  const body = worker.slice(worker.indexOf("function strategyFor"));

  const apiCheck = body.indexOf("API_PREFIX");
  const documentCheck = body.indexOf('"document"');
  const immutableCheck = body.indexOf("IMMUTABLE_PREFIX");

  assert.ok(apiCheck > -1 && documentCheck > -1 && immutableCheck > -1);
  assert.ok(apiCheck < documentCheck, "the API guard must come before the document guard");
  assert.ok(apiCheck < immutableCheck, "the API guard must come before the asset guards");
});

test("the shipped worker never caches a response it did not check", async () => {
  const { readFile } = await import("node:fs/promises");
  const worker = await readFile(new URL("../public/sw.js", import.meta.url), "utf8");

  // Every cache.put must be behind an isCacheable check. Counting is crude and
  // it is the property that matters: a put that skipped the check is how a 502
  // becomes permanent.
  const puts = worker.match(/cache\.put\(/g) ?? [];
  const guards = worker.match(/isCacheable\(/g) ?? [];
  assert.ok(puts.length > 0, "the worker caches nothing at all");
  // One definition plus one guard per put.
  assert.equal(guards.length, puts.length + 1);
});

test("the shipped worker only ever handles GET", async () => {
  const { readFile } = await import("node:fs/promises");
  const worker = await readFile(new URL("../public/sw.js", import.meta.url), "utf8");
  // Replaying a POST from a cache would repeat an upload rather than re-read a
  // value.
  assert.match(worker, /request\.method !== "GET"/);
});

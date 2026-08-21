/**
 * The Content-Security-Policy, asserted where it is decided.
 *
 * Two different things are checked here, and they fail for different reasons.
 *
 * **The policy string** is a pure function of a nonce, an origin and a flag, so
 * every claim about it — that scripts are nonce-gated, that `'unsafe-eval'`
 * never reaches production, that no directive is written twice — is settled
 * without a browser.
 *
 * **The couplings** are read out of the files that carry them. A CSP is unlike
 * most code in that breaking it produces no error where the mistake is: delete
 * one line in `app/layout.tsx` and every build still succeeds, every test that
 * imports a module still passes, and the application is a blank page in a
 * browser because the nonce in the header no longer matches anything in the
 * HTML. Both couplings below were observed failing exactly that way during
 * Step 10.9, which is why they are asserted from the files rather than trusted
 * to a comment.
 *
 * What this file cannot establish is that the policy does not break the running
 * application. That was measured in Chromium against the real stack — upload,
 * speech analysis, audio measurement, the musical key card and the identity
 * panel, with the microphone path exercised separately — and the numbers are in
 * `docs/architecture.md`.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { connectOrigin, contentSecurityPolicy, createNonce } from "../lib/csp.ts";

const NONCE = "T2h5b3Vmb3VuZHRoaXM=";

function policy(overrides: Partial<Parameters<typeof contentSecurityPolicy>[0]> = {}) {
  return contentSecurityPolicy({
    nonce: NONCE,
    connectOrigin: null,
    development: false,
    ...overrides,
  });
}

/** The policy as `{ directive: sources }`, which is how a browser reads it. */
function directives(value: string): Map<string, string[]> {
  const parsed = new Map<string, string[]>();
  for (const part of value.split(";")) {
    const [name, ...sources] = part.trim().split(/\s+/);
    assert.ok(!parsed.has(name), `${name} is written twice; a browser honours only the first`);
    parsed.set(name, sources);
  }
  return parsed;
}

function read(relative: string): string {
  return readFileSync(fileURLToPath(new URL(`../${relative}`, import.meta.url)), "utf8");
}

/**
 * A file with its comments removed.
 *
 * An assertion about what is *absent* has to look at code only. `proxy.ts`
 * explains at length why it has no `matcher`, so a plain search for that word
 * fails on the very comment documenting its absence — which is how the first
 * version of this test failed, and how the equivalent test failed in
 * `backend/tests/test_deployment.py`.
 */
function code(relative: string): string {
  return read(relative)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

// --- The nonce --------------------------------------------------------------

test("a nonce carries 128 bits of randomness", () => {
  const value = createNonce();

  // 16 bytes, base64 — the length the CSP specification recommends. A short
  // nonce is a policy an attacker can enumerate offline.
  assert.equal(Buffer.from(value, "base64").length, 16);
});

test("no two nonces are the same", () => {
  const seen = new Set(Array.from({ length: 500 }, () => createNonce()));

  assert.equal(seen.size, 500);
});

// --- Scripts ----------------------------------------------------------------

test("scripts are admitted by the request's own nonce", () => {
  assert.ok(directives(policy()).get("script-src")?.includes(`'nonce-${NONCE}'`));
});

test("scripts injected into the markup are refused even from our own origin", () => {
  // `'strict-dynamic'` makes a browser ignore `'self'` for scripts: a
  // `<script src>` that appears in the HTML is refused however same-origin it
  // looks, and only scripts loaded *by* an already-trusted script get through.
  // That is what carries Next.js's chunks and the AudioWorklet module.
  assert.ok(directives(policy()).get("script-src")?.includes("'strict-dynamic'"));
});

test("no inline script is ever allowed without the nonce", () => {
  for (const development of [false, true]) {
    const script = directives(policy({ development })).get("script-src") ?? [];

    // `'unsafe-inline'` would not merely weaken this policy — a browser that
    // sees a nonce *ignores* `'unsafe-inline'`, so writing it would be a
    // decorative source that silently does nothing. Either way it must not
    // appear.
    assert.ok(!script.includes("'unsafe-inline'"), `development=${development}`);
  }
});

test("development is the only place `eval` is tolerated", () => {
  // `next dev` compiles modules with `eval`. Measured with it forbidden: every
  // module evaluation raised a `script-src` violation and the page never
  // hydrated, so a policy without this makes `npm run dev` unusable.
  assert.ok(directives(policy({ development: true })).get("script-src")?.includes("'unsafe-eval'"));
  assert.ok(!directives(policy()).get("script-src")?.includes("'unsafe-eval'"));
});

test("development and production differ in exactly one source", () => {
  const production = directives(policy()).get("script-src") ?? [];
  const development = directives(policy({ development: true })).get("script-src") ?? [];

  assert.deepEqual(
    development.filter((source) => !production.includes(source)),
    ["'unsafe-eval'"],
  );
});

// --- Everything else --------------------------------------------------------

test("the page may reach the API when it answers on another origin", () => {
  const parsed = directives(policy({ connectOrigin: "http://localhost:8000" }));

  assert.deepEqual(parsed.get("connect-src"), ["'self'", "http://localhost:8000"]);
});

test("nothing but the page's own origin is named when the API shares it", () => {
  assert.deepEqual(directives(policy()).get("connect-src"), ["'self'"]);
});

test("a recorded take can be played back from memory", () => {
  // `lib/live-pitch-engine.ts` builds the WAV in the browser and plays it
  // through an object URL, which `media-src` has to admit by scheme.
  assert.deepEqual(directives(policy()).get("media-src"), ["'self'", "blob:"]);
});

test("styles are the one concession, and only styles", () => {
  const parsed = directives(policy());

  // React writes `style` attributes for measured widths, and
  // `app/global-error.tsx` carries its own `<style>` element so it survives the
  // stylesheet failing. Neither can hold a nonce.
  assert.deepEqual(parsed.get("style-src"), ["'self'", "'unsafe-inline'"]);
  for (const [name, sources] of parsed) {
    if (name === "style-src") continue;
    assert.ok(!sources.includes("'unsafe-inline'"), name);
  }
});

test("what the product never loads is forbidden rather than left to a default", () => {
  const parsed = directives(policy());

  assert.deepEqual(parsed.get("object-src"), ["'none'"]);
  assert.deepEqual(parsed.get("base-uri"), ["'none'"]);
  // No `<form>` exists in this product; the upload is a `fetch` with a
  // `FormData` body, which `form-action` does not govern.
  assert.deepEqual(parsed.get("form-action"), ["'none'"]);
  assert.deepEqual(parsed.get("frame-ancestors"), ["'none'"]);
  assert.deepEqual(parsed.get("default-src"), ["'self'"]);
});

test("images and fonts come from this origin and nowhere else", () => {
  const parsed = directives(policy());

  // Checked against the build output rather than assumed: `next/font`
  // self-hosts the font files, and the compiled CSS contains no `url(data:…)`
  // and no external origin.
  assert.deepEqual(parsed.get("img-src"), ["'self'"]);
  assert.deepEqual(parsed.get("font-src"), ["'self'"]);
});

test("the policy makes no claim about transport security", () => {
  // The bundled deployment speaks HTTP and its proxy advertises no HSTS for
  // that reason. `upgrade-insecure-requests` would contradict it and break a
  // plain-HTTP deployment.
  assert.ok(!policy().includes("upgrade-insecure-requests"));
});

test("the policy points at no reporting endpoint, because none exists", () => {
  assert.ok(!policy().includes("report-uri"));
  assert.ok(!policy().includes("report-to"));
});

test("the policy is one header line", () => {
  assert.ok(!/[\r\n]/.test(policy()));
});

// --- Origins ----------------------------------------------------------------

test("an API on the page's own origin adds nothing to the policy", () => {
  assert.equal(connectOrigin("http://localhost:3000/api", "http://localhost:3000"), null);
});

test("an API elsewhere is named by origin, without its path", () => {
  assert.equal(connectOrigin("http://api.example.test:8000/api/v1", "https://example.test"), "http://api.example.test:8000");
});

test("a misconfigured API URL never reaches the header", () => {
  // A value that is not a URL must not be pasted into a security header, and
  // must not throw inside a request either. It degrades to `'self'`, and the
  // browser's console reports the connection it then refuses.
  assert.equal(connectOrigin("not a url", "https://example.test"), null);
  assert.equal(connectOrigin("", "https://example.test"), null);
});

// --- The couplings ----------------------------------------------------------

test("the root layout still renders per request", () => {
  // Without this, pages are prerendered — built before the nonce exists and
  // served with none. Measured on this application with the route static:
  // Chromium refused all ten of the page's script elements and the HTML sat
  // there inert. The failure is silent everywhere except a browser.
  assert.match(read("app/layout.tsx"), /export const dynamic = "force-dynamic";/);
});

test("the policy is set on the request as well as on the response", () => {
  const proxy = read("proxy.ts");

  // The request header is how Next.js learns the nonce and stamps it onto the
  // script elements it renders. Set only the response header and the policy
  // forbids the application's own scripts — measured, and identically fatal to
  // dropping the line above.
  assert.match(proxy, /headers\.set\(CSP_HEADER, policy\)/);
  assert.match(proxy, /response\.headers\.set\(CSP_HEADER, policy\)/);
  assert.match(proxy, /NextResponse\.next\(\{ request: \{ headers \} \}\)/);
});

test("the policy applies to every request, with no exceptions list", () => {
  // A `matcher` is a list of exceptions, and every exception is a document that
  // could leave without a policy.
  assert.ok(!code("proxy.ts").includes("matcher"));
});

// --- Installability (Step 12) ----------------------------------------------

test("a service worker may be registered, and only from this origin", () => {
  const parsed = directives(policy());
  assert.deepEqual(parsed.get("worker-src"), ["'self'"]);
});

test("worker-src is named rather than left to fall back to script-src", () => {
  // The fallback would be fatal, not merely loose. `script-src` carries
  // `'strict-dynamic'`, which makes a browser ignore `'self'` and demand a
  // nonce — and a worker script has no way to carry one. Naming `worker-src`
  // is what lets the page keep `'strict-dynamic'` and still register a worker.
  const parsed = directives(policy());
  assert.ok(parsed.has("worker-src"));
  assert.ok(parsed.get("script-src")?.includes("'strict-dynamic'"));
  assert.ok(!parsed.get("worker-src")?.includes("'strict-dynamic'"));
});

test("the manifest may be fetched, and only from this origin", () => {
  const parsed = directives(policy());
  assert.deepEqual(parsed.get("manifest-src"), ["'self'"]);
});

test("installability widened the policy by two directives and no sources", () => {
  // Both new directives are `'self'` alone. Making the app installable must not
  // have been an excuse to admit an origin, and this is what says so.
  const parsed = directives(policy());
  for (const name of ["worker-src", "manifest-src"]) {
    assert.deepEqual(parsed.get(name), ["'self'"], name);
  }
});

test("the AudioWorklet is still governed by script-src, not by worker-src", () => {
  // `worker-src` covers Worker, SharedWorker and ServiceWorker. An AudioWorklet
  // has its own fetch destination and stays under `script-src`, where
  // `'strict-dynamic'` admits it because a trusted script loads it. Both
  // directives are needed and neither replaces the other; that adding one did
  // not break the other was measured in Chromium.
  const parsed = directives(policy());
  assert.ok(parsed.get("script-src")?.includes("'strict-dynamic'"));
  assert.deepEqual(parsed.get("worker-src"), ["'self'"]);
});

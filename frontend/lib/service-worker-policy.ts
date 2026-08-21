/**
 * What the service worker may cache, and what it must never cache.
 *
 * **This module is the specification; `public/sw.js` is the implementation.**
 * They are separate for the reason `pitch.ts` and `pitch.py` are: a service
 * worker is a classic script in its own global scope with no bundler in front
 * of it, and routing this through the build to avoid a second copy would be a
 * worse trade than writing the rules down once and holding both to them. The
 * rules are asserted here by `node --test`, and the *running* worker is driven
 * through a real Chromium in the browser check — see `docs/architecture.md`.
 *
 * Three rules, and two of them are refusals.
 *
 * **Never an API response.** This product's central rule is that a number shown
 * is a number measured. A cached analysis is a number that *was* measured, some
 * time ago, presented as if it were current — and the client cannot tell the
 * difference. Re-analysing a recording changes its numbers, deleting a
 * reference changes a comparison, and a cache that served the old answer would
 * make this application lie in exactly the way the rest of it refuses to. There
 * is no staleness budget that makes that acceptable, so there is no cache.
 *
 * **Never a document**, and the reason is not the one it first appears to be.
 *
 * The obvious argument is that every HTML response carries a fresh per-request
 * CSP nonce, so a cached document would carry a stale one and the browser would
 * refuse every script on the page. **That argument is wrong, and it was wrong
 * in this file until it was measured.** A cache stores a response *with its
 * headers*, so the nonce in the cached header still matches the nonce in the
 * cached body: replayed exactly as a service worker would serve it, the page
 * produced zero CSP violations, hydrated, and answered a click. The nonce goes
 * stale together, which is to say it does not go stale at all.
 *
 * The real cost is what caching does to the nonce's *purpose*. A per-request
 * nonce is unguessable because it is fresh every time; a cached shell serves one
 * fixed nonce for as long as the entry lives, turning a per-request value into a
 * durable per-device one. `app/layout.tsx` gave up the full-route cache
 * specifically to keep that property, and a service worker that quietly took it
 * back would be overturning a decision made one layer up without saying so.
 *
 * What that costs is real and is worth naming: Live Vocal Practice runs entirely
 * in the browser and would work offline from a cached shell. It is given up
 * deliberately, not overlooked — see `docs/limitations.md`.
 *
 * **Cache the assets whose URL changes when their content does.** Everything
 * under `/_next/static/` is content-hashed by the build, so a URL that resolves
 * once resolves to the same bytes forever and may be served from a cache
 * without checking. That is where an installed app's speed actually comes from.
 *
 * Everything else — the icons, the AudioWorklet — is a stable URL over
 * changeable content, so it is revalidated in the background rather than
 * trusted: served from the cache for speed, and replaced for next time.
 */

/** Where the caching decision can land. */
export type CacheStrategy =
  /** Go to the network, and never write the response down. */
  | "never"
  /** Serve from the cache without asking; the URL is a content hash. */
  | "cache-first"
  /** Serve from the cache, and refresh it in the background for next time. */
  | "stale-while-revalidate"
  /** Go to the network; if it cannot be reached, show the offline page. */
  | "offline-fallback";

/** The prefix every versioned API path shares. Never cached. */
export const API_PREFIX = "/api/";

/** Content-hashed build output. Immutable by construction. */
export const IMMUTABLE_PREFIX = "/_next/static/";

/** Stable URLs whose bytes can change between deploys. */
export const REVALIDATED_PATHS = ["/icons/", "/pcm-capture-worklet.js"] as const;

/** The scriptless page shown when a navigation cannot reach the network. */
export const OFFLINE_URL = "/offline.html";

/**
 * How a request should be served.
 *
 * Takes the path and the request's `destination` rather than a whole `Request`,
 * so it is callable from a test without constructing one and from the worker
 * without adapting one.
 *
 * `destination` is `"document"` for a navigation. Checking that rather than
 * `mode === "navigate"` also catches a document fetched some other way, and it
 * is the field the worker has to hand.
 */
export function cacheStrategyFor(pathname: string, destination: string): CacheStrategy {
  // First, and before anything else can claim it. A rule that came after the
  // asset rules would be a rule an asset-looking API path could slip past.
  if (pathname.startsWith(API_PREFIX)) return "never";

  if (destination === "document") return "offline-fallback";

  if (pathname.startsWith(IMMUTABLE_PREFIX)) return "cache-first";

  if (REVALIDATED_PATHS.some((prefix) => pathname.startsWith(prefix))) {
    return "stale-while-revalidate";
  }

  // Anything not named above is served from the network and not written down.
  // A default of "cache it" is how a cache comes to hold something nobody
  // decided it should hold.
  return "never";
}

/**
 * Whether a response is worth putting in the cache.
 *
 * A `Response` that is not `ok` must never be stored: caching a 404 or a 502
 * turns a transient failure into a durable one, and the only way back out is
 * for the user to clear site data. Opaque cross-origin responses are refused
 * for a related reason — their status is unreadable, so "is it ok" cannot be
 * answered — and this application loads nothing cross-origin anyway.
 */
export function isCacheable(status: number, type: string): boolean {
  return status >= 200 && status < 300 && type !== "opaque";
}

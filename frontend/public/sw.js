/*
 * VocalLens service worker.
 *
 * The implementation of the rules written down in `lib/service-worker-policy.ts`,
 * which is the specification and carries the reasoning. The two are separate
 * because a service worker is a classic script in its own global scope with no
 * bundler in front of it; the rules are unit-tested there and the *running*
 * worker is driven through a real Chromium in the browser check.
 *
 * In short, and the short version is mostly refusals:
 *
 *   - never cache anything under /api/ — a cached measurement is a lie
 *   - never cache a document — not because a stale nonce would break it (it
 *     would not; that was measured) but because a cached shell freezes one
 *     nonce in place, and the layout gave up caching to keep it per-request
 *   - cache /_next/static/ without asking, because those URLs are content hashes
 *   - revalidate the icons and the worklet in the background
 *   - when a navigation cannot reach the network, show a scriptless offline page
 *
 * There is no precached application shell. That is a decision with a cost —
 * Live Vocal Practice would work offline from one — and both the decision and
 * the measurement behind it are in docs/architecture.md.
 */

// Bump to invalidate everything. The activate handler deletes every cache whose
// name is not this one, so a deploy that changes the version starts clean and
// cannot serve a mix of two builds.
const CACHE = "vocallens-v1";

const API_PREFIX = "/api/";
const IMMUTABLE_PREFIX = "/_next/static/";
const REVALIDATED_PATHS = ["/icons/", "/pcm-capture-worklet.js"];
const OFFLINE_URL = "/offline.html";

/** Mirrors `cacheStrategyFor` in lib/service-worker-policy.ts. */
function strategyFor(pathname, destination) {
  if (pathname.startsWith(API_PREFIX)) return "never";
  if (destination === "document") return "offline-fallback";
  if (pathname.startsWith(IMMUTABLE_PREFIX)) return "cache-first";
  if (REVALIDATED_PATHS.some((prefix) => pathname.startsWith(prefix))) {
    return "stale-while-revalidate";
  }
  return "never";
}

/** Mirrors `isCacheable` in lib/service-worker-policy.ts. */
function isCacheable(response) {
  return response.status >= 200 && response.status < 300 && response.type !== "opaque";
}

self.addEventListener("install", (event) => {
  // Only the offline page. Precaching the icons too would be a nicety; the
  // offline page is the one asset that is useless if it has to be fetched at
  // the moment it is needed.
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.add(new Request(OFFLINE_URL, { cache: "reload" })))
      // A failed precache must not abort the install: the worker is still
      // worth having for the static assets, and the offline page would only be
      // missing from a state that has no network anyway.
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;

  // Only GET. A POST is an upload or an analysis being started, and replaying
  // one from a cache would repeat an action rather than re-read a value.
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // Another origin's response is not ours to store or to reason about, and this
  // application loads nothing cross-origin.
  if (url.origin !== self.location.origin) return;

  const strategy = strategyFor(url.pathname, request.destination);

  // Not calling respondWith at all is deliberate: it leaves the request to the
  // browser exactly as if no worker existed, which is a weaker claim to make
  // than fetching it ourselves and a smaller thing to get wrong.
  if (strategy === "never") return;

  if (strategy === "offline-fallback") {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match(OFFLINE_URL).then(
          (cached) =>
            cached ??
            new Response("You are offline.", {
              status: 503,
              headers: { "Content-Type": "text/plain; charset=utf-8" },
            }),
        ),
      ),
    );
    return;
  }

  if (strategy === "cache-first") {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ??
          fetch(request).then((response) => {
            if (isCacheable(response)) {
              const copy = response.clone();
              caches.open(CACHE).then((cache) => cache.put(request, copy));
            }
            return response;
          }),
      ),
    );
    return;
  }

  // stale-while-revalidate
  event.respondWith(
    caches.match(request).then((cached) => {
      const fresh = fetch(request)
        .then((response) => {
          if (isCacheable(response)) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached);
      // Serve what we have and let the refresh finish behind it; with nothing
      // cached, wait for the network rather than inventing an answer.
      return cached ?? fresh;
    }),
  );
});

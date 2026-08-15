/**
 * The Content-Security-Policy this application serves.
 *
 * Step 10.5 shipped three response headers at the edge proxy and deliberately
 * shipped no CSP, for a reason recorded in three places: Next.js emits inline
 * `<script>` elements, so a policy strict enough to be worth having needs a
 * fresh nonce per request, and a speculative one would either be bypassable or
 * would break the product. This module is the nonce half of that sentence, and
 * `proxy.ts` is the per-request half.
 *
 * Everything here is a pure function of its arguments so the policy can be
 * asserted directly, without a browser and without a running server. What a
 * unit test cannot establish — that the policy does not break the running
 * application — was measured against a real Chromium; see
 * `docs/architecture.md`.
 *
 * ## Why each directive is what it is
 *
 * Every source below is here because something in this repository loads it.
 * Nothing is listed "just in case": an allowance nobody uses is an allowance
 * nobody notices when it starts being used.
 *
 * - `script-src` carries the nonce and `'strict-dynamic'`. The nonce covers the
 *   two inline scripts Next.js emits — the bootstrap and the flight payload —
 *   and `'strict-dynamic'` covers the chunks those scripts then load, including
 *   the `pcm-capture-worklet.js` AudioWorklet module, which is fetched by
 *   script rather than by the parser. `'strict-dynamic'` also makes the browser
 *   *ignore* `'self'` for scripts, so a `<script src>` injected into the markup
 *   is refused even though it points at our own origin. `'self'` is kept for
 *   browsers too old to understand `'strict-dynamic'`, where it degrades to a
 *   same-origin policy rather than to nothing.
 * - `style-src` keeps `'unsafe-inline'`, and that is the one real concession in
 *   this policy. The alternative — `style-src 'self' 'nonce-…'`, which is what
 *   Next.js's own guide suggests — was built and measured rather than argued
 *   about, and the measurement was not what the argument predicted. The three
 *   components that set a `style` attribute for something they measured (a
 *   progress width, a bar length) **kept working**: all twenty declarations
 *   still applied and nothing was reported, because React writes them through
 *   the CSSOM after hydration and the CSSOM is outside CSP's reach. What broke
 *   was `app/global-error.tsx`: its `<style>` element raised a
 *   `style-src-elem` violation and did not apply, leaving the last-resort
 *   failure page unstyled — the one page whose whole job is to work when
 *   everything else has not.
 *
 *   What that concession costs is bounded by the rest of this policy rather
 *   than by trust. Injected CSS cannot execute — `script-src` sees to that —
 *   and it cannot exfiltrate either, because the classic CSS side channel is an
 *   attribute selector with a `url()` in it and `img-src`, `font-src` and
 *   `connect-src` all refuse every destination off this origin. What remains is
 *   defacement of a page an attacker already had to inject HTML into. That is
 *   stated in `docs/limitations.md` rather than left implicit.
 * - `connect-src` names the configured API origin as well as `'self'`, because
 *   in development the API answers on another port. Behind the bundled proxy
 *   the two are the same origin and this adds nothing.
 * - `media-src` allows `blob:` for the recorded take: `lib/live-pitch-engine.ts`
 *   builds a WAV in memory and plays it back through an object URL.
 * - `img-src` and `font-src` are `'self'` alone. `next/font` self-hosts the
 *   font files at build time, and the built CSS contains no `url(data:…)` and
 *   no external origin — both checked against the build output rather than
 *   assumed.
 * - `form-action 'none'` because this product submits no form. The upload is a
 *   `fetch` with a `FormData` body, which this does not affect.
 * - No `upgrade-insecure-requests`, for the same reason the proxy advertises no
 *   HSTS: this deployment speaks HTTP, and a policy that claimed otherwise
 *   would break it.
 * - No `report-uri` / `report-to`. Nothing in this system collects reports, and
 *   a directive pointing at an endpoint that does not exist is decoration.
 */

/** Bytes of randomness behind each nonce. 128 bits, base64-encoded. */
const NONCE_BYTES = 16;

export interface PolicyOptions {
  /** The per-request nonce, base64, without the `nonce-` prefix. */
  nonce: string;
  /**
   * An extra origin the page may connect to, or `null` when the API shares the
   * page's origin. Produced by {@link connectOrigin}.
   */
  connectOrigin: string | null;
  /**
   * Development relaxes exactly one directive. `next dev` compiles with `eval`,
   * so a production-strength `script-src` stops the dev server dead — measured:
   * every module evaluation raised a `script-src` violation and the page never
   * hydrated. Relaxing it in development keeps `npm run dev` usable; production
   * never sees it, and the test suite asserts that.
   */
  development: boolean;
}

/**
 * A fresh nonce.
 *
 * `crypto.getRandomValues` rather than `Math.random`: a guessable nonce is a
 * policy an attacker can satisfy. 128 bits is the CSP specification's own
 * recommendation.
 */
export function createNonce(): string {
  const bytes = new Uint8Array(NONCE_BYTES);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}

/**
 * The origin `connect-src` must name so the browser may reach the API, or
 * `null` when naming it would add nothing.
 *
 * `null` covers both the case where the API shares the page's origin — which is
 * what the bundled deployment does, since nginx serves `/api/` from the same
 * host — and the case where the configured value is not a URL at all. A
 * malformed value must not be pasted into a security header, and it must not
 * throw inside a request either, so it degrades to `'self'` and the browser's
 * console reports the blocked connection.
 */
export function connectOrigin(apiBaseUrl: string, pageOrigin: string): string | null {
  let origin: string;
  try {
    origin = new URL(apiBaseUrl).origin;
  } catch {
    return null;
  }
  return origin === pageOrigin ? null : origin;
}

/** The policy, as it is sent. */
export function contentSecurityPolicy(options: PolicyOptions): string {
  const script = [
    "'self'",
    `'nonce-${options.nonce}'`,
    "'strict-dynamic'",
    ...(options.development ? ["'unsafe-eval'"] : []),
  ];

  const connect = ["'self'", ...(options.connectOrigin ? [options.connectOrigin] : [])];

  return [
    "default-src 'self'",
    `script-src ${script.join(" ")}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self'",
    "font-src 'self'",
    "media-src 'self' blob:",
    `connect-src ${connect.join(" ")}`,
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join("; ");
}

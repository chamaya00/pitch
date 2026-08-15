/**
 * The per-request half of the Content-Security-Policy.
 *
 * **The name is Next.js's, not this project's.** Next 16 renamed the
 * `middleware` file convention to `proxy`, and the framework only looks for
 * this filename at the application root. It has nothing to do with the nginx
 * edge proxy in `deploy/nginx/`, which is a different program in a different
 * language solving a different problem, and which deliberately sets **no** CSP
 * — see `docs/architecture.md`. Two `Content-Security-Policy` headers are not a
 * stricter policy, they are two policies enforced at once, and an edge policy
 * written without a nonce would forbid exactly the scripts the nonce below
 * permits.
 *
 * What this does is the whole reason a useful CSP was deferred in 10.5: mint a
 * nonce per request, put it in the *request* headers so Next.js stamps it onto
 * every script element it renders, and repeat it in the response header so the
 * browser knows to accept those and nothing else.
 *
 * The request header is what makes it work. Next.js reads the nonce out of the
 * incoming `Content-Security-Policy` header — that is the documented seam — and
 * threads it through the renderer. Setting only the response header produces a
 * policy the application's own scripts fail: measured in Chromium, all ten
 * script elements were refused and the page never hydrated.
 *
 * It also means **pages must render per request**, which is why
 * `app/layout.tsx` declares `dynamic = "force-dynamic"`. A prerendered page is
 * HTML built before the nonce existed, and it is served with whatever nonce it
 * was built with — which is to say, none. That was measured too, on this same
 * application, and it is recorded beside the decision in `docs/architecture.md`
 * rather than only here.
 *
 * There is no `matcher`. A matcher is a list of exceptions, and every exception
 * is a response that leaves without a policy; the work per request is one
 * 16-byte random draw and a string join.
 */

import { NextResponse, type NextRequest } from "next/server";

import { API_BASE_URL } from "@/lib/config";
import { connectOrigin, contentSecurityPolicy, createNonce } from "@/lib/csp";

export const CSP_HEADER = "Content-Security-Policy";

export function proxy(request: NextRequest): NextResponse {
  const policy = contentSecurityPolicy({
    nonce: createNonce(),
    connectOrigin: connectOrigin(API_BASE_URL, request.nextUrl.origin),
    development: process.env.NODE_ENV !== "production",
  });

  const headers = new Headers(request.headers);
  headers.set(CSP_HEADER, policy);

  const response = NextResponse.next({ request: { headers } });
  response.headers.set(CSP_HEADER, policy);
  return response;
}

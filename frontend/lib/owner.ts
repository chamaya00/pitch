/**
 * The browser's half of the anonymous owner identity.
 *
 * The server issues a token in the `X-VocalLens-Owner` response header the
 * first time it sees a request without one. This module stores that token and
 * sends it back on every later request, which is the whole reason a returning
 * visitor sees their own history rather than an empty one.
 *
 * **This is an identifier, not a login**, and the copy in the UI says so. There
 * is no password and no recovery: clearing site data or switching browsers
 * produces a new identity with an empty history, exactly as if it were a first
 * visit. Saying that plainly is better than implying an account exists.
 *
 * Deliberately `localStorage` and not a cookie. A cookie would be sent on every
 * request to the origin automatically, including ones this code does not make,
 * and would need `SameSite`/`Secure` reasoning to avoid becoming a
 * cross-site-request-forgery surface. An explicit header is sent only where it
 * is meant to be sent.
 */

/** Storage key. Namespaced so it cannot collide with anything else on the origin. */
export const OWNER_STORAGE_KEY = "vocallens.owner";

/** The header carrying the identity, in both directions. */
export const OWNER_HEADER = "X-VocalLens-Owner";

/**
 * The server's token shape: 22 characters of URL-safe base64.
 *
 * Checked on the way *out* of storage as well as in. A corrupted or
 * hand-edited value would otherwise be sent and rejected with a validation
 * error on every request, which reads as "the app is broken" rather than "this
 * value is not a token".
 */
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{22}$/;

/** Storage that may not exist: SSR has no `window`, and private modes can throw. */
type MaybeStorage = Pick<Storage, "getItem" | "setItem" | "removeItem"> | null;

function storage(): MaybeStorage {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage;
  } catch {
    // Blocked by a browser setting. Everything below degrades to "no stored
    // identity", which means a fresh one per session — not a crash.
    return null;
  }
}

/** The stored token, or `null` if there is none and none is usable. */
export function readOwnerToken(): string | null {
  const store = storage();
  if (!store) return null;
  let value: string | null;
  try {
    value = store.getItem(OWNER_STORAGE_KEY);
  } catch {
    return null;
  }
  if (value === null) return null;
  if (!TOKEN_PATTERN.test(value)) {
    // Not something this server issued. Drop it rather than sending it: an
    // invalid token is a 422 on every request until it is cleared.
    clearOwnerToken();
    return null;
  }
  return value;
}

/**
 * Store a token the server just minted.
 *
 * Refuses anything that is not a token, so a malformed header — from a proxy,
 * a stub, or a bug — cannot poison storage for every later request.
 */
export function storeOwnerToken(token: string): boolean {
  if (!TOKEN_PATTERN.test(token)) return false;
  const store = storage();
  if (!store) return false;
  try {
    store.setItem(OWNER_STORAGE_KEY, token);
    return true;
  } catch {
    // Quota exceeded, or storage disabled mid-session.
    return false;
  }
}

export function clearOwnerToken(): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(OWNER_STORAGE_KEY);
  } catch {
    // Nothing useful to do, and nothing depends on it having worked.
  }
}

/** The identity header to add to a request, or nothing on a first visit. */
export function ownerHeaders(): Record<string, string> {
  const token = readOwnerToken();
  return token === null ? {} : { [OWNER_HEADER]: token };
}

/**
 * Store the token from a response, if it carried one.
 *
 * The server sends this header **only** when it mints a new identity, so an
 * absent header is the normal case and not a failure.
 */
export function captureOwnerToken(headers: {
  get(name: string): string | null;
}): void {
  const minted = headers.get(OWNER_HEADER);
  if (minted) storeOwnerToken(minted);
}

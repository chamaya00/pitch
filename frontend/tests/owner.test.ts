/**
 * The browser's half of the owner identity.
 *
 * Two failure modes are worth more than the rest, and both are silent:
 *
 * * a token that is stored but never sent — every request mints a new identity
 *   and history never accumulates, with no error anywhere;
 * * a value that is *not* a token being stored and sent — a 422 on every
 *   request until site data is cleared, which reads as "the app is broken".
 *
 * `localStorage` does not exist under Node, so a minimal stand-in is installed
 * on `globalThis` before the module under test reads it. That is the same
 * object the browser supplies, restricted to the three methods this code uses.
 */

import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import {
  OWNER_HEADER,
  OWNER_STORAGE_KEY,
  captureOwnerToken,
  clearOwnerToken,
  ownerHeaders,
  readOwnerToken,
  storeOwnerToken,
} from "../lib/owner.ts";

/** 22 characters of URL-safe base64, the shape the server issues. */
const VALID = "abcdefghijklmnopqrstuv";
const ALSO_VALID = "ABCDEFGHIJKLMNOPQRST-_";

class MemoryStorage {
  private readonly entries = new Map<string, string>();
  /** Set to make every write throw, as a full or disabled store does. */
  failWrites = false;

  getItem(key: string): string | null {
    return this.entries.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    if (this.failWrites) throw new Error("QuotaExceededError");
    this.entries.set(key, value);
  }

  removeItem(key: string): void {
    this.entries.delete(key);
  }

  has(key: string): boolean {
    return this.entries.has(key);
  }
}

let store: MemoryStorage;

beforeEach(() => {
  store = new MemoryStorage();
  (globalThis as { window?: unknown }).window = { localStorage: store };
});

afterEach(() => {
  delete (globalThis as { window?: unknown }).window;
});

/** A `Headers`-alike, which is all `captureOwnerToken` requires. */
function headers(values: Record<string, string>) {
  return { get: (name: string) => values[name] ?? null };
}

// --- Reading and writing ---------------------------------------------------

test("a stored token round-trips", () => {
  assert.equal(storeOwnerToken(VALID), true);
  assert.equal(readOwnerToken(), VALID);
});

test("nothing is stored on a first visit", () => {
  assert.equal(readOwnerToken(), null);
});

test("the header is omitted entirely when there is no token", () => {
  assert.deepEqual(ownerHeaders(), {});
});

test("the header carries the stored token", () => {
  storeOwnerToken(VALID);
  assert.deepEqual(ownerHeaders(), { [OWNER_HEADER]: VALID });
});

test("clearing removes the token", () => {
  storeOwnerToken(VALID);
  clearOwnerToken();
  assert.equal(readOwnerToken(), null);
});

test("a second token replaces the first rather than accumulating", () => {
  storeOwnerToken(VALID);
  storeOwnerToken(ALSO_VALID);
  assert.equal(readOwnerToken(), ALSO_VALID);
});

// --- Refusing what cannot be a token ---------------------------------------

for (const bad of [
  "",
  "short",
  "a".repeat(21),
  "a".repeat(23),
  "has spaces in it here!",
  "../../../etc/passwd123",
  '{"json":"value"}aaaaaa',
]) {
  test(`a malformed value is not stored: ${JSON.stringify(bad)}`, () => {
    assert.equal(storeOwnerToken(bad), false);
    assert.equal(readOwnerToken(), null);
    assert.deepEqual(ownerHeaders(), {});
  });
}

test("a corrupted stored value is dropped rather than sent", () => {
  // Written directly, as a hand-edited or half-migrated store would be.
  store.setItem(OWNER_STORAGE_KEY, "not-a-token");

  assert.equal(readOwnerToken(), null);
  assert.equal(store.has(OWNER_STORAGE_KEY), false, "it must be cleared, not just ignored");
});

// --- Capturing a minted token ----------------------------------------------

test("a minted token from a response is stored", () => {
  captureOwnerToken(headers({ [OWNER_HEADER]: VALID }));
  assert.equal(readOwnerToken(), VALID);
});

test("a response without the header is the normal case, not a failure", () => {
  storeOwnerToken(VALID);
  captureOwnerToken(headers({}));
  assert.equal(readOwnerToken(), VALID, "an absent header must not clear the identity");
});

test("a malformed header value cannot poison storage", () => {
  storeOwnerToken(VALID);
  captureOwnerToken(headers({ [OWNER_HEADER]: "garbage-from-a-proxy" }));
  assert.equal(readOwnerToken(), VALID);
});

// --- Degrading rather than crashing ----------------------------------------

test("storage that refuses writes degrades to no identity", () => {
  store.failWrites = true;

  assert.equal(storeOwnerToken(VALID), false);
  assert.equal(readOwnerToken(), null);
  assert.deepEqual(ownerHeaders(), {});
});

test("no window at all is a first visit, not an exception", () => {
  delete (globalThis as { window?: unknown }).window;

  assert.equal(readOwnerToken(), null);
  assert.equal(storeOwnerToken(VALID), false);
  assert.deepEqual(ownerHeaders(), {});
  clearOwnerToken();
});

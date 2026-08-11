/**
 * What the error boundaries are allowed to say.
 *
 * The boundaries in `app/` render only what this module returns, so this is
 * where the security property lives: an exception can carry a stack trace, a
 * filesystem path, a database DSN, a bearer key or somebody's owner id, and
 * none of it may reach the screen. The functions below are handed a
 * deliberately poisonous error and must return exactly the same fixed copy they
 * return for anything else.
 *
 * The components themselves are verified in the browser: `node --test` runs
 * with `--experimental-strip-types`, which removes TypeScript annotations but
 * does not transform JSX, so a `.tsx` file cannot be imported here. That is the
 * same split every other component in this project uses — pure logic in `lib/`
 * tested here, rendering proved in Chromium.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  allPresentations,
  presentGlobalFailure,
  presentNotFound,
  presentRouteFailure,
} from "../lib/error-presentation.ts";

/** Everything a real exception might drag along with it. */
const POISON = Object.assign(
  new Error(
    "connection to server at \"10.0.0.4\", port 5432 failed: FATAL: password authentication failed for user \"vocallens\"",
  ),
  {
    digest: "3948571029384756",
    stack: [
      "Error: boom",
      "    at PostgresOwnerRepository.get (/home/user/pitch/backend/app/services/owners/repository.py:114:9)",
      "    at /var/lib/vocallens/recordings/9f2c.wav",
    ].join("\n"),
    // The kind of thing that must never be echoed, in the shapes it really has.
    ownerId: "1c2e3c68-b724-4920-9f7b-8b7f37ac2c9e",
    token: "Yb3xK9pQ2mLrT8wZ4nE1aQ",
    credentialHash: "bd7bccb79e50bf9833579223a1ced3fbe843ee759e61db470ed24c024f5f2849",
    apiKey: "sk-ant-api03-not-a-real-key",
    dsn: "postgresql://vocallens:vocallens@db:5432/vocallens",
  },
);

/** Every substring that must never appear in anything shown to a person. */
const FORBIDDEN = [
  "10.0.0.4",
  "5432",
  "password authentication",
  "PostgresOwnerRepository",
  "/home/user/pitch",
  "/var/lib/vocallens",
  "repository.py",
  "1c2e3c68",
  "Yb3xK9pQ2mLrT8wZ4nE1aQ",
  "bd7bccb7",
  "sk-ant",
  "postgresql://",
  "3948571029384756",
  "Error:",
  "at ",
  "boom",
];

function textOf(presentation: {
  title: string;
  body: string;
  retryLabel: string | null;
  homeLabel: string;
}): string {
  return [presentation.title, presentation.body, presentation.retryLabel ?? "", presentation.homeLabel].join(
    " ",
  );
}

// --- Nothing from the error reaches the copy --------------------------------

for (const [name, present] of [
  ["route", () => presentRouteFailure(POISON)],
  ["global", () => presentGlobalFailure(POISON)],
] as const) {
  test(`the ${name} boundary shows nothing from the error object`, () => {
    const shown = textOf(present());

    for (const secret of FORBIDDEN) {
      assert.ok(
        !shown.includes(secret),
        `"${secret}" from the error reached the copy: ${shown}`,
      );
    }
  });

  test(`the ${name} boundary's copy does not vary with the error`, () => {
    // The strongest form of the guarantee: two utterly different failures are
    // indistinguishable on screen, so nothing about either can be inferred.
    const fromPoison = textOf(present());
    const fromNothing = textOf(
      name === "route" ? presentRouteFailure(undefined) : presentGlobalFailure(undefined),
    );
    const fromString = textOf(
      name === "route"
        ? presentRouteFailure("owner 1c2e3c68 has no credential")
        : presentGlobalFailure("owner 1c2e3c68 has no credential"),
    );

    assert.equal(fromPoison, fromNothing);
    assert.equal(fromPoison, fromString);
  });
}

test("no presentation contains developer vocabulary", () => {
  for (const presentation of allPresentations()) {
    const shown = textOf(presentation).toLowerCase();
    for (const jargon of [
      "exception",
      "stack",
      "trace",
      "undefined",
      "null",
      "typeerror",
      "digest",
      "500",
      "console",
      "hydration",
    ]) {
      assert.ok(!shown.includes(jargon), `"${jargon}" is developer vocabulary: ${shown}`);
    }
  }
});

// --- Each state says something usable ---------------------------------------

test("every presentation has a heading, a body and a way out", () => {
  for (const presentation of allPresentations()) {
    assert.ok(presentation.title.length > 0);
    assert.ok(presentation.body.length > 0);
    assert.ok(presentation.homeLabel.length > 0);
  }
});

test("a broken page offers a retry; a wrong address does not", () => {
  assert.equal(presentRouteFailure(POISON).retryLabel, "Try again");
  assert.equal(presentGlobalFailure(POISON).retryLabel, "Reload the page");
  // Re-requesting a missing address fails identically, so offering a retry
  // would be pretending there is something to recover.
  assert.equal(presentNotFound().retryLabel, null);
});

test("not-found is not presented as a failure of the application", () => {
  const shown = textOf(presentNotFound()).toLowerCase();

  assert.ok(!shown.includes("went wrong"));
  assert.ok(!shown.includes("failed"));
  assert.match(shown, /doesn't exist|not (be )?found/);
});

test("not-found and a broken page do not read the same", () => {
  assert.notEqual(presentNotFound().title, presentRouteFailure(POISON).title);
  assert.notEqual(presentNotFound().body, presentRouteFailure(POISON).body);
});

test("a broken page says the recordings are safe, because they are", () => {
  for (const presentation of [presentRouteFailure(POISON), presentGlobalFailure(POISON)]) {
    assert.match(presentation.body, /unaffected/);
  }
});

test("no copy blames the person reading it", () => {
  for (const presentation of allPresentations()) {
    const shown = textOf(presentation).toLowerCase();
    for (const blame of ["you broke", "invalid request", "you did", "your fault", "illegal"]) {
      assert.ok(!shown.includes(blame), `"${blame}" blames the reader: ${shown}`);
    }
  }
});

test("the not-found copy never echoes an address back", () => {
  const shown = textOf(presentNotFound());

  // Nothing URL-shaped: repeating an arbitrary address into the page is how a
  // 404 turns into a reflected-content problem.
  assert.ok(!/https?:\/\/|\/[a-z]+\//i.test(shown), shown);
});

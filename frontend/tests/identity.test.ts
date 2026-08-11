/**
 * Recovery-key wording and rules.
 *
 * The key is the only thing between somebody and their whole history, and the
 * server cannot replace it. So the rules under test are the ones that decide
 * whether that history survives: a mistyped key must be refused rather than
 * stored, and the warnings must be concrete enough to be read.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DELETE_CONFIRMATION,
  deletionWarning,
  formatIssued,
  isDeletionConfirmed,
  isRecoveryKey,
  recoveryKeyProblem,
  summarise,
} from "../lib/identity.ts";
import type { Identity } from "../types/api.ts";

function identity(overrides: Partial<Identity> = {}): Identity {
  return {
    created_at: "2026-08-01T10:00:00Z",
    anonymous: true,
    recordings: 5,
    analysed_recordings: 4,
    ai_feedback: 1,
    ...overrides,
  };
}

const VALID = "abcdefghijklmnopqrstuv";

// --- Refusing a key that cannot be one -------------------------------------

test("a well-formed key is accepted", () => {
  assert.equal(isRecoveryKey(VALID), true);
  assert.equal(recoveryKeyProblem(VALID), null);
});

test("surrounding whitespace is tolerated, since keys get pasted", () => {
  assert.equal(recoveryKeyProblem(`  ${VALID}\n`), null);
});

for (const bad of [
  "",
  "   ",
  "short",
  "a".repeat(21),
  "a".repeat(23),
  "has spaces in it here!",
  "../../../etc/passwd123",
]) {
  test(`a malformed key is refused: ${JSON.stringify(bad)}`, () => {
    assert.equal(isRecoveryKey(bad), false);
    assert.notEqual(recoveryKeyProblem(bad), null);
  });
}

test("an empty field is told what to do, not told it is malformed", () => {
  assert.match(recoveryKeyProblem("") ?? "", /Paste the key/);
  assert.match(recoveryKeyProblem("nope") ?? "", /doesn't look like/);
});

// --- Saying what is at stake -----------------------------------------------

test("an empty key says so plainly", () => {
  assert.match(summarise(identity({ recordings: 0 })), /no recordings yet/);
});

test("the summary counts what would be lost", () => {
  const text = summarise(identity());

  assert.match(text, /5 recordings/);
  assert.match(text, /4 measured/);
  assert.match(text, /1 with generated feedback/);
});

test("a single recording is singular", () => {
  assert.match(
    summarise(identity({ recordings: 1, analysed_recordings: 0, ai_feedback: 0 })),
    /1 recording\./,
  );
});

test("generated feedback is only mentioned when there is some", () => {
  const none = summarise(identity({ ai_feedback: 0 }));

  assert.ok(!none.includes("generated feedback"));
});

// --- The deletion warning --------------------------------------------------

test("the warning is concrete rather than asking about 'everything'", () => {
  const warning = deletionWarning(identity());

  assert.match(warning, /5 recordings/);
  assert.match(warning, /stored audio/);
  assert.match(warning, /cannot be undone/);
});

test("the warning names generated feedback as unrecoverable", () => {
  assert.match(deletionWarning(identity({ ai_feedback: 2 })), /cannot be recovered/);
});

test("the warning omits feedback when there is none", () => {
  const warning = deletionWarning(identity({ ai_feedback: 0 }));

  assert.ok(!warning.includes("generated feedback"));
});

test("nothing to delete says nothing to delete", () => {
  assert.match(
    deletionWarning(identity({ recordings: 0, analysed_recordings: 0, ai_feedback: 0 })),
    /nothing to delete/,
  );
});

// --- Confirming --------------------------------------------------------------

test("deletion requires the exact word", () => {
  assert.equal(isDeletionConfirmed(DELETE_CONFIRMATION), true);
  assert.equal(isDeletionConfirmed("DELETE"), true, "case is forgiven, spelling is not");
  assert.equal(isDeletionConfirmed("  delete "), true);
});

for (const wrong of ["", "yes", "delete everything", "del", "remove"]) {
  test(`deletion is not confirmed by ${JSON.stringify(wrong)}`, () => {
    assert.equal(isDeletionConfirmed(wrong), false);
  });
}

// --- Dates -------------------------------------------------------------------

test("the issue date renders readably, not as the raw string", () => {
  const rendered = formatIssued("2026-08-01T10:00:00Z");

  assert.notEqual(rendered, "2026-08-01T10:00:00Z");
  assert.match(rendered, /2026/);
});

test("an unparseable date says so rather than rendering NaN", () => {
  assert.equal(formatIssued("not a date"), "Unknown date");
});

// --- Nothing claims the server can help ---------------------------------------

test("no wording implies the key can be recovered or reset", () => {
  const surfaces = [
    summarise(identity()),
    deletionWarning(identity()),
    recoveryKeyProblem("") ?? "",
    recoveryKeyProblem("nope") ?? "",
  ].join(" ").toLowerCase();

  for (const phrase of ["reset", "email you", "send you a new", "forgot", "recover your key"]) {
    assert.ok(!surfaces.includes(phrase), `"${phrase}" implies help the server cannot give`);
  }
});

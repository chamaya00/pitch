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
  describeKey,
  formatIssued,
  holdsNothing,
  isDeletionConfirmed,
  isRecoveryKey,
  recoveryKeyProblem,
  revocationProblem,
  revocationWarning,
  summarise,
} from "../lib/identity.ts";
import type { Credential, Identity } from "../types/api.ts";

function credential(overrides: Partial<Credential> = {}): Credential {
  return {
    credential_id: "6f1c9d0e-0000-4000-8000-000000000001",
    label: "Phone",
    created_at: "2026-08-02T09:00:00Z",
    current: false,
    ...overrides,
  };
}

function identity(overrides: Partial<Identity> = {}): Identity {
  return {
    created_at: "2026-08-01T10:00:00Z",
    anonymous: true,
    recordings: 5,
    analysed_recordings: 4,
    ai_feedback: 1,
    song_references: 0,
    credentials: [],
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
  // "Nothing", not "no recordings": a recording stopped being the only thing a
  // key can hold, and the two sentences stopped meaning the same thing.
  assert.match(summarise(identity({ recordings: 0 })), /nothing yet/);
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


// --- Keys --------------------------------------------------------------------

test("a key is described by its label and date, never by its value", () => {
  const described = describeKey(credential());

  assert.match(described, /Phone/);
  assert.match(described, /2026/);
  // Nothing in a Credential is credential material, and the id is not shown
  // either: it is a handle for revoking, not something to read out.
  assert.ok(!described.includes("6f1c9d0e"));
});

test("the key this browser holds says so", () => {
  assert.match(describeKey(credential({ current: true })), /this browser/);
});

test("the only key cannot be revoked, and the reason is given", () => {
  const problem = revocationProblem([credential({ current: true })]);

  assert.notEqual(problem, null);
  assert.match(problem ?? "", /only way in/);
});

test("a key can be revoked once another exists", () => {
  assert.equal(
    revocationProblem([credential(), credential({ credential_id: "other" })]),
    null,
  );
});

test("the current key can be revoked when another exists", () => {
  const all = [credential({ current: true }), credential({ credential_id: "other" })];

  assert.equal(revocationProblem(all), null);
});

test("revoking is described as removing a way in, not as deleting data", () => {
  const warning = revocationWarning(credential()).toLowerCase();

  assert.match(warning, /recordings, measurements and feedback all stay/);
  for (const phrase of ["delete your recordings", "permanently", "cannot be undone"]) {
    assert.ok(!warning.includes(phrase), `"${phrase}" overstates what revoking does`);
  }
});

test("revoking the key in use warns that this browser will need another", () => {
  assert.match(revocationWarning(credential({ current: true })), /other keys/);
});

test("no key wording implies a second identity", () => {
  const surfaces = [
    describeKey(credential()),
    revocationWarning(credential()),
    revocationProblem([credential()]) ?? "",
  ]
    .join(" ")
    .toLowerCase();

  for (const phrase of ["new account", "second account", "another account", "sign in"]) {
    assert.ok(!surfaces.includes(phrase), `"${phrase}" implies an account system`);
  }
});

// --- What a key holds, when it holds something that is not a recording ------

test("a key holding only songs does not say it holds nothing", () => {
  // Until song references existed, "no recordings" and "nothing" were the same
  // sentence. They are not, and this key is the only way back to those songs.
  const held = identity({ recordings: 0, analysed_recordings: 0, song_references: 2 });
  assert.equal(summarise(held), "This key holds 2 songs you described.");
});

test("a key holding neither says so", () => {
  const empty = identity({ recordings: 0, analysed_recordings: 0, ai_feedback: 0 });
  assert.equal(summarise(empty), "This key holds nothing yet.");
});

test("a key holding both counts both, and songs are not counted as recordings", () => {
  const held = identity({
    recordings: 3,
    analysed_recordings: 2,
    ai_feedback: 0,
    song_references: 1,
  });
  assert.match(summarise(held), /3 recordings, 2 measured, 1 song you described/);
});

test("deleting a key that holds only songs is not described as deleting nothing", () => {
  const held = identity({ recordings: 0, analysed_recordings: 0, song_references: 3 });
  const warning = deletionWarning(held);
  assert.doesNotMatch(warning, /nothing to delete/);
  assert.match(warning, /3 songs you described/);
});

test("deleting a key that holds recordings names the songs as well", () => {
  const held = identity({ recordings: 2, song_references: 1 });
  assert.match(deletionWarning(held), /also deletes 1 song you described/);
});

test("a key that truly holds nothing still says there is nothing to delete", () => {
  const empty = identity({ recordings: 0, analysed_recordings: 0, ai_feedback: 0 });
  assert.match(deletionWarning(empty), /nothing to delete/);
});

test("holding nothing means holding nothing at all", () => {
  assert.equal(holdsNothing(identity({ recordings: 0, ai_feedback: 0 })), true);
  assert.equal(holdsNothing(identity({ recordings: 0, song_references: 1 })), false);
  assert.equal(holdsNothing(identity({ recordings: 1 })), false);
});

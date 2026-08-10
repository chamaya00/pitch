# Speech analysis

How a spoken recording becomes numbers, and — just as important — which numbers
it deliberately does not become.

This document covers the deterministic layer. `docs/ai.md` covers the language
model's role, which is to explain what is measured here and nothing else.

## Status

Implemented (Step 7B): the domain model, the metrics, the provider boundary and
mock providers.

Implemented (Step 7C): real provider adapters for Deepgram and Claude, the
configuration and factory that select between them, and error translation.

Implemented (Step 7D): the orchestration service, analysis persistence, failure
and idempotency behaviour.

Implemented (Step 7E): the HTTP API — see [api.md](api.md).

Not implemented: the analysis UI.

**No real transcription has been executed.** The adapters exist and are covered
by tests at the SDK boundary, but this development environment has no provider
credentials and its network policy blocks the Deepgram host outright, so no
request has ever reached either service from here. See *Verification status*
below.

## Providers

| Role | Real provider | Mock |
| --- | --- | --- |
| Speech to text | Deepgram (`services/ai/deepgram.py`) | `MockSpeechToTextProvider` |
| Qualitative feedback | Anthropic Claude (`services/ai/claude.py`) | `MockFeedbackProvider` |

Two vendors, because one cannot do both jobs — Claude has no audio input.

Selection is configuration, resolved once in `services/ai/factory.py`:

| Variable | Values | Default |
| --- | --- | --- |
| `SPEECH_TO_TEXT_PROVIDER` | `mock`, `deepgram` | `mock` |
| `FEEDBACK_PROVIDER` | `mock`, `claude` | `mock` |
| `DEEPGRAM_API_KEY` | — | unset |
| `DEEPGRAM_MODEL` | — | `nova-3` |
| `DEEPGRAM_FILLER_WORDS` | `true`, `false` | `false` |
| `ANTHROPIC_API_KEY` | — | unset |
| `ANTHROPIC_MODEL` | — | `claude-opus-5` |
| `ANALYSIS_PROVIDER_TIMEOUT_SECONDS` | — | `120` |

`mock` is the default so a fresh checkout runs without credentials. It is not a
quiet default: the active provider names appear in the startup log and in
`Settings.public_config()`, and a `mock_analysis_providers_active` warning is
logged on boot.

**There is no fallback.** Selecting `deepgram` without `DEEPGRAM_API_KEY` (or
`claude` without `ANTHROPIC_API_KEY`) raises `ANALYSIS_NOT_CONFIGURED`. A
configured provider that is unavailable produces the matching error code. Under
no circumstances does a real provider degrade into a mock — an operator who
asked for Deepgram and received demo data labelled as a result would have no way
to tell.

## What leaves the application

| Destination | Sent | Never sent |
| --- | --- | --- |
| Deepgram | The recording's audio bytes | Filename, recording id, user identity, any other key |
| Claude | The transcript text (capped at 12,000 characters) and the computed metrics | Audio, any path, filename, recording id, user identity, any other key |

Claude never receives audio. That is enforced by the `FeedbackProvider`
protocol, whose signature takes a transcript and metrics and has no path or
bytes in it at all.

**Retention is provider-dependent and has not been verified for this project.**
Whether Deepgram or Anthropic retains a submitted recording or transcript, and
for how long, depends on the account, plan and contract in force — none of which
could be checked from this environment. Do not describe either provider's
retention behaviour to users until it has been confirmed against the provider's
own documentation and your account settings.

## Verification status

| Check | Result |
| --- | --- |
| Deepgram request against the live API | **Not verified** — no credentials, and the host is blocked by this environment's network policy (403 at the proxy on CONNECT) |
| Claude request against the live API | **Not verified** — no credentials (`api.anthropic.com` is reachable and answers 401) |
| Request construction | Verified against the installed SDKs' real signatures |
| Response mapping | Verified through the SDKs' own response models and parsers |
| Failure translation | Verified for every documented failure class |

The adapter tests fake the SDK boundary — one callable each — so everything on
this side of the wire is the real code path. What they cannot show is that a
live provider returns what the fixtures contain.

## Shape of the pipeline

```
recording  ──▶  SpeechToTextProvider  ──▶  Transcript
                                              │
                                              ▼
                                     metrics.compute_metrics()   (pure, deterministic)
                                              │
                                              ▼
                                        SpeechMetrics
                                              │
                                              ▼
                            FeedbackProvider (text + numbers only)  ──▶  Feedback
```

Two providers, because one cannot do both jobs: Claude has no audio input and
no speech-to-text endpoint, so it can only ever be the second. The
`FeedbackProvider` protocol takes a transcript and metrics and has no path or
bytes in its signature, which keeps that boundary enforced rather than merely
documented.

The measurement step sits between them and uses no model at all. Feedback that
mentions a number is repeating one this layer produced.

## Orchestration

`services/orchestration/analysis.py` is the only place that spans the packages.
It takes a recording id and nothing else, and it is expressed entirely in terms
of the protocols — swapping either provider for a mock changes nothing in it.

It is split in two so an API route can answer immediately:

| Method | Does |
| --- | --- |
| `start(recording_id)` | Validates the recording, applies the idempotency rules, returns a `pending` record. Fast — no provider call. |
| `run(analysis_id)` | Executes the pipeline to a terminal state. Slow; intended for a `BackgroundTask`. |
| `analyze(recording_id)` | Both, for tests and synchronous use. |

**The record is written before every slow step, not after.** A process killed
mid-analysis leaves a record saying where it got to, rather than vanishing.

### Persistence

One JSON document per analysis at `<storage-root>/analyses/<analysis_id>.json`,
written temp file → flush → fsync → atomic rename, exactly as recordings are.
`create` publishes with `link` and so refuses to overwrite; `update` publishes
with `replace` and requires the record to exist. That distinction matters: a
`create` that clobbered an in-flight analysis would destroy a transcript that
cost a paid provider call.

`list_for_recording` is a directory scan, not an index. It is the clearest sign
that this store is a stopgap — its cost grows with every analysis ever run — and
it is accepted because the alternative is a database with extra steps.

### Failure

| Situation | Outcome |
| --- | --- |
| Recording unknown | `RECORDING_NOT_FOUND`; **no** analysis record is created |
| Transcription fails | `failed` record carrying the provider's error code |
| No speech in the recording | `failed` record with `TRANSCRIPT_EMPTY` |
| Feedback fails | `completed` record, `feedback = None` |
| Anything unforeseen | `failed` record with `INTERNAL_ERROR` |

`run` never raises for a provider failure — a background task must not be able
to take the process down. Cancellation is the exception: it is persisted and
then re-raised, because swallowing it would leave the loop believing the task
is still running.

**The recording is never deleted or modified because an analysis failed.** A
failed analysis stays on disk as the record of what went wrong; a retry creates
a new analysis rather than overwriting it.

A feedback failure degrades an analysis to numbers without prose. Turning a
successful transcription into a total failure because a language model was
unavailable would throw away the part of the result that is actually measured.
The failure is logged; the domain model has no field for a partial failure, and
inventing one to hold it would be worse than the log line.

### Idempotency

`start` returns an existing analysis rather than beginning a second one:

| Existing analysis | Result |
| --- | --- |
| `pending` / `transcribing` / `analyzing` | Returned as-is; no provider call |
| `completed` | Returned as-is; no provider call |
| `failed`, or none | A new `pending` record |

An analysis that has been in flight since before
`ANALYSIS_STALE_AFTER_SECONDS` belongs to a process that is no longer running.
It is swept to `failed` (`INTERNAL_ERROR`) so the recording does not become
permanently unanalysable, and it stays inspectable.

The find-or-create decision is serialised process-wide. That is not a
substitute for a database constraint — with more than one worker process the
race returns, which is a known limit of the filesystem store.

## Algorithmic choices

### Word count

Tokens are `[0-9A-Za-z]+` with internal apostrophes, so `don't` is one word and
trailing punctuation is never part of one. Provider word segmentation is used
when the provider supplies it, falling back to tokenising the full text.

This is the only metric that is always available — a transcript with no timings
still has words.

### Pause threshold: 0.5 s

A gap of 0.5 s or more between two words is a pause. Below roughly 0.2 s a gap
is ordinary articulation, and the speech literature puts silent pauses somewhere
in the 0.25–0.5 s range. We took the conservative end deliberately: the cost of
a threshold that is too low is telling someone they hesitated when they simply
phrased a sentence.

The threshold is a parameter, and whichever value was used is stored on the
result. A pause count without its threshold is not interpretable.

Gaps are computed in timing order rather than list order, and overlapping words
— which some providers emit — yield no gap rather than a negative one.

### Speaking rate: words per minute

Words per minute, not syllables per second. Syllable counting needs a
pronunciation dictionary we do not have, and an English-only heuristic would be
silently wrong on the first non-English recording.

Two different denominators are possible, and which one was used is recorded in
`duration_source`:

- `word_timings` — first word start to last word end. Excludes silence at the
  head and tail of the file.
- `recording_duration` — the file's own duration, used only when the provider
  returned no timings. Includes that silence, so the rate reads lower.

They are not interchangeable, which is why the source travels with the number.

### Articulation rate

The same word count over the same span with detected pause time removed. It
needs word timings, because without them there are no pauses to remove. If
pauses account for the entire span, the metric is omitted rather than divided by
zero.

### Filler words

Two categories, kept separate on purpose:

- **Hesitations** — `um`, `uh`, `er`, `erm`, `ah`, `eh`, `hmm`, `mmm`, plus
  spelling variants collapsed onto a canonical term. These sounds have no other
  meaning, so counting them is a measurement.
- **Discourse markers** — `like`, `you know`, `I mean`, `sort of`, `kind of`,
  `basically`, `literally`. Ordinary words that are *sometimes* fillers. "I like
  this" is not hesitation. Counting them lexically is a word-frequency count and
  must be labelled as one wherever it is shown.

Phrases are matched longest-first and consume the tokens they match, so
`you know` is counted once rather than also contributing a bare `know`.

The discourse-marker list is short on purpose. Every entry added to it is
another chance to tell someone they hesitated when they simply spoke.

## Missing data is missing

Every metric that the source data may not support is `X | None`, and it is left
`None` when it cannot be computed. There is no default speaking rate, no `0`
standing in for "we don't know", and no invented confidence.

The distinction that matters most:

| Situation | Result |
| --- | --- |
| No word timings | `pause_count` is `None` — nothing to detect |
| Timings, no gap long enough | `pause_count` is `0` — a measured zero |
| No pause found | `longest_pause_seconds` and `mean_pause_seconds` are `None` — the maximum and mean of an empty set are undefined |
| Provider cleans disfluencies | `filler_words` is `None` — not measurable |
| Verbatim provider, no fillers said | `filler_words` present with counts of `0` |

The filler case is the one most likely to be got wrong. Most speech-to-text
providers strip disfluencies by default, so counting against a cleaned
transcript would report "no fillers" for a recording full of them. A transcript
therefore carries `includes_disfluencies`, defaulting to `False`, and filler
statistics are omitted entirely unless a provider sets it.

### How Deepgram sets that flag

Deepgram accepts a `filler_words` option but does not echo back whether it
applied it, so the flag cannot come from a single response field. The adapter
requires two things before marking a transcript verbatim:

1. `DEEPGRAM_FILLER_WORDS=true` — an operator asserting that the configured
   model and plan support the option.
2. The model Deepgram reports having actually run (`metadata.model_info`) is one
   known to honour it. A request routed elsewhere logs
   `disfluencies_requested_but_unconfirmed` and the transcript is marked
   non-verbatim.

Either check failing means filler statistics are omitted. That is the safe
direction: the cost of being wrong here is telling someone they said no filler
words when they said many.

## Provenance

Every piece of generated content carries a `Provenance`: the provider's short
label, the model it named, and `is_mock`. It is a required field with no
default, so a transcript or a piece of feedback cannot exist in this system
without saying where it came from. `Analysis.provenance` aggregates the two and
is derived rather than stored, so it cannot drift from the content it describes.

Presentation layers key their "demo data — not real analysis" treatment off
`is_mock`. A mock result must never be presented as genuine analysis.

`Provenance` forbids extra fields and constrains its values to narrow label
patterns. That is the one place an adapter might be tempted to stash "just the
request id" — or the key it used — and a bearer token cannot satisfy either
pattern.

## Failure handling

Provider failures are raised as `ProviderError` subclasses, each mapping to one
error code (see `docs/api.md`). The vendor's own text — exception class,
response body, URL, request id — never reaches a response: `str(exc)` is our
generic wording, and a short `reason` classifier goes to the logs only,
truncated, and explicitly must not be a raw provider payload.

An analysis that fails always records *why*; one that succeeds never carries a
stale error code. Both are enforced by the model's validator rather than by
convention.

`Feedback` is not required for an analysis to complete. The numbers are produced
without a language model, so an outage at the feedback provider degrades an
analysis to "numbers without prose" rather than losing it. The reverse is
refused: feedback cannot be attached without the metrics it claims to describe.

## What is deliberately not measured

- **Pronunciation or accent scoring.** It needs a provider that offers assessed
  phoneme scores. Approximating it from a transcript would be inventing a
  number, and the resulting score would fall hardest on the accents least
  represented in the model's training data.
- **Confidence in a speaker's ability.** Metrics describe one recording.
- **Anything clinical.** No vocal-health inference, no diagnosis, no
  "professional assessment".
- **Sentiment, personality or credibility.** Not measurable from these inputs.

## Mock providers

The mocks exist so the pipeline can be built and exercised before any real
provider. They do not transcribe and they do not reason.

`MockSpeechToTextProvider` reads the recording only to derive a SHA-256-based
seed from its bytes, then returns one of three fixed demo scripts laid out on a
synthetic timeline. The same file always produces the same transcript; different
files produce different ones. Each script says what it is in its opening
sentence, so a mock transcript that somehow reached a screen would still read as
one, and each contains hesitations and sentence boundaries so the metrics have
something real to work on.

`MockFeedbackProvider` assembles feedback from a template. Every sentence it can
produce is guarded by the metric it describes, so a metric that was not
measurable produces no sentence — the same rule a real feedback provider has to
follow.

## Notes for whoever adds another provider

- Set `includes_disfluencies` only if the provider genuinely returns verbatim
  output. Most do not by default.
- Providers receive an already-resolved `Path` from `RecordingStorage`. Never
  build a path from a recording id, a client filename, or anything in a provider
  response.
- Translate every vendor failure into a `ProviderError` subclass. Nothing else
  should escape a provider.
- Never fall back from a real provider to a mock. A failure is a failure; a mock
  result silently substituted for a real one is the one outcome this design
  exists to prevent.
- Stream the recording rather than reading it into memory: uploads are allowed
  up to 50 MB.
- One attempt per call, and `max_retries=0` on the vendor client. Whether a
  failed analysis is retried is orchestration's decision, and authentication and
  invalid-request failures must never be retried automatically.

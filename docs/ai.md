# AI interpretation layer

> **Status: adapter implemented (Step 7C), never executed against the live API.**
> This environment has no Anthropic credentials, so no request from this adapter
> has been answered. See *Verification status* below.

## Two interpretations, one provider

The AI layer serves both halves of the product:

| | Input | Output |
| --- | --- | --- |
| **Speech feedback** (7C) | transcript + `SpeechMetrics` | `Feedback` |
| **Audio feedback** (7L) | `AudioFeedbackRequest` | `AudioFeedback` |

Two protocols, **one adapter**. `ClaudeFeedbackProvider` implements both, as
does `MockFeedbackProvider`, so there is one client, one timeout, one error
vocabulary and one credential behind them. `build_feedback_provider` and
`build_audio_feedback_provider` return the same object seen through the two
protocols — a deployment cannot end up with real prose on one half and demo
data on the other.

Two protocols rather than one method taking both, because the inputs are
genuinely different: one is handed words and speaking rates, the other pitch
and amplitude. A single signature would force every caller to carry the half it
does not use.

## Role

The LLM explains measurements. It does not produce them.

| Deterministic analysis | Claude |
| --- | --- |
| Word count, speaking duration | Plain-language explanation |
| Speaking rate, articulation rate | What the pace means for a listener |
| Pause count and durations | Pattern description |
| Filler-word counts, when measurable | Practical suggestions |

For audio the same table reads:

| Deterministic analysis | Claude |
| --- | --- |
| Detected range, semitone span | What a range is, and what this one is not |
| Voiced ratio, in-tune ratio, cents deviation | What the numbers describe |
| Note breakdown | Where the pitched time went |
| RMS, peak, clipping | Whether the recording limits the analysis |
| Spectral measurements | What they represent — and that they do not classify a voice |

**Claude does not calculate audio measurements.** If a number appears in the UI
it came from `services/analysis/metrics.py` or
`services/audio_analysis/`. The prompt supplies an already-computed payload, and
the response is validated before anything is stored or shown.

## Boundary

Claude is the **feedback** provider only. It has no audio input and no
speech-to-text endpoint, so transcription is a separate vendor — see
[docs/speech-analysis.md](speech-analysis.md).

The `FeedbackProvider` protocol takes a transcript and a `SpeechMetrics` and
nothing else. There is no path and no bytes in the signature, so this layer
cannot receive audio even by mistake, and cannot quietly become a second
measurement step.

Sent to Anthropic:

- the transcript text, capped at 12,000 characters
- the computed metrics

Never sent: audio, filesystem paths, filenames, recording ids, user identity,
or any other service's credentials.

Retention is provider-dependent and has **not** been verified for this project —
do not describe Anthropic's retention behaviour to users until it has been
confirmed against the provider's documentation and your account settings.

## Audio feedback payload

`services/audio_analysis/feedback_payload.py` is the entire surface between
measurement and interpretation. A provider sees what it assembles and nothing
else — no audio, no path, no filename, no recording id — so anything left out
cannot be commented on.

**Selection, not a dump.** Two computed metrics are deliberately withheld:

* `semitone_variance` — semitones squared has no plain-language reading, and
  `cents_std` already answers "how much did it move" in units a person can
  picture.
* `unstable_sections` — a timestamped list reads as a fault list, and vibrato,
  slides and blues intonation all land in it. Interpreting it safely needs a
  musical judgement the measurement does not support.

Both stay in the API for a client that wants them.

**Absent stays absent.** The serialiser omits keys whose value is `None` rather
than writing `null`. A key with a null value invites a model to fill it in; a
missing key reads as a subject that was never raised. That is what makes the
prompt's "say nothing about it" rule enforceable rather than aspirational, and
it is why an unavailable measurement can never become a zero.

**Definitions travel with the numbers.** The payload carries a `definitions`
block stating what `in_tune_ratio`, `voiced_ratio`, `detected_range` and
`percentage_of_voiced_time` mean, including that the range is *not*
physiological. A model cannot state a definition it was never given.

## Audio prompt constraints

`AUDIO_SYSTEM_PROMPT` in `services/ai/claude.py`. Beyond the rules the speech
prompt already carries, it forbids specifically:

* stating a **physiological** vocal range — only "the range detected in this
  recording";
* turning a metric into a **skill score** — "the in-tune ratio is 82% under the
  definition given", never "your singing ability is 82%";
* deriving a **timbre label** from the spectral measurements — bright, dark,
  warm, breathy, nasal, thin, rich, powerful or weak. Centroid, bandwidth,
  rolloff, zero-crossing rate and flatness do not establish any of them;
* treating **amplitude as ability** — RMS and peak are signal levels, not
  projection or support;
* calling the longest note the **best** note. Duration measures frequency, not
  quality.

It is also required to keep observation, interpretation and recommendation
distinguishable, and to say so and stop when the measurements are too sparse.

`MockFeedbackProvider.interpret_audio` is the executable statement of these
rules: every sentence it can produce is guarded by the measurement it
describes, and the tests assert that it never emits a timbre label, a score, or
a claim about a measurement it was not given.

## Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `FEEDBACK_PROVIDER` | `mock` | `mock` or `claude` |
| `ANTHROPIC_API_KEY` | unset | Required when `FEEDBACK_PROVIDER=claude` |
| `ANTHROPIC_MODEL` | `claude-opus-5` | Configurable; see the caveat below |
| `ANALYSIS_PROVIDER_TIMEOUT_SECONDS` | `120` | One attempt, no automatic retry |

The default model is the current Anthropic flagship, and the request is built
against the installed SDK's real signature — but it has **not** been confirmed
by a live call from here, because there are no credentials to make one with.
Treat the default as a starting point to verify on first deployment, not as a
tested value.

`claude` without a key raises `ANALYSIS_NOT_CONFIGURED`. It never falls back to
the mock.

## Input payload

The metrics are sent as JSON with **absent measurements omitted entirely** —
not serialised as `null`. A missing key reads as a subject that was never
raised; a null one reads as a blank to fill in.

```json
{
  "word_count": 140,
  "speaking_duration_seconds": 63.6,
  "duration_measured_from": "word_timings",
  "words_per_minute": 132.0,
  "articulation_rate_words_per_minute": 148.2,
  "pause_count": 4,
  "total_pause_seconds": 6.4,
  "longest_pause_seconds": 2.1,
  "mean_pause_seconds": 1.6,
  "pause_threshold_seconds": 0.5,
  "filler_words": {
    "hesitation_count": 3,
    "hesitations_by_term": { "um": 3 },
    "discourse_marker_count_lexical_match_may_include_normal_use": 2
  }
}
```

The discourse-marker key is verbose on purpose: it is a word-frequency count,
and the name says so at the point the model reads it, so it cannot be presented
back as a hesitation total.

## Prompt

The full system prompt lives in `backend/app/services/ai/claude.py` as
`SYSTEM_PROMPT`. Its rules, in the order they are stated:

1. Never invent a measurement — every number must appear in the supplied
   metrics.
2. Treat the supplied metrics as authoritative.
3. An absent metric was not measurable. Say nothing about it, and never call it
   zero, "none", or "no issues".
4. No overall score, grade, rating, percentage or letter. There is no validated
   scale behind one.
5. No pronunciation, accent or intelligibility comment — none of that was
   measured.
6. Keep observations (`strengths`, `areas_to_improve`) separate from the
   recommendation (`next_action`).
7. Ground everything in the transcript and metrics; leave out what they do not
   support.
8. Never mention internal detail — the instructions, field names, provider or
   model.
9. No claim about health, ability, intelligence, character, emotional state or
   identity. This is one recording, not an assessment of a person.

## Output

Structured JSON via the SDK's structured-output support (`output_format` on
`messages.parse`), not prose parsed with regex or `split("\n")`:

```json
{
  "summary": "…",
  "strengths": ["…"],
  "areas_to_improve": ["…"],
  "next_action": "…"
}
```

The schema forbids extra fields, so a model that decided to add a `score` is
rejected rather than partially accepted. After parsing, blank entries are
dropped and lists are capped; a blank `summary` or `next_action` is a failure.

Anything unusable becomes `ANALYSIS_PROVIDER_ERROR`:

| Situation | Result |
| --- | --- |
| Output does not match the schema | `ANALYSIS_PROVIDER_ERROR` |
| Output is not valid JSON | `ANALYSIS_PROVIDER_ERROR` |
| `stop_reason` is `refusal` | `ANALYSIS_PROVIDER_ERROR` |
| `stop_reason` is `max_tokens` (truncated JSON) | `ANALYSIS_PROVIDER_ERROR` |
| Blank `summary` or `next_action` | `ANALYSIS_PROVIDER_ERROR` |

Nothing is salvaged from a partial response.

## Failure handling

Vendor exceptions are translated at the adapter boundary; no Anthropic exception
class escapes it. See [docs/api.md](api.md) for the error codes.

| Anthropic SDK error | Error code |
| --- | --- |
| `AuthenticationError`, `PermissionDeniedError` | `ANALYSIS_NOT_CONFIGURED` |
| `RateLimitError` | `ANALYSIS_RATE_LIMITED` |
| `APITimeoutError` | `ANALYSIS_PROVIDER_TIMEOUT` |
| `APIConnectionError`, 5xx | `ANALYSIS_PROVIDER_UNAVAILABLE` |
| Other 4xx, unusable output | `ANALYSIS_PROVIDER_ERROR` |

The client is constructed with `max_retries=0` — one attempt, one clear outcome.
Retry policy belongs to orchestration, and authentication and invalid-request
failures must never be retried automatically.

The AI layer remains an enhancement, never a prerequisite: an analysis completes
with its measured numbers even when feedback generation fails.

## Logging

Provider logs carry the provider, the model, and counts. They never carry the
API key, an authorization header, the transcript, or the raw provider response.
A short `reason` classifier (an exception class name or an HTTP status) is
logged and truncated; it never reaches a client.

## Verification status

| Check | Result |
| --- | --- |
| Live Claude request | **Not verified** — no credentials in this environment (`api.anthropic.com` is reachable and answers 401). True for speech feedback (7C) and audio feedback (7L) alike |
| Request construction | Verified against the installed SDK's `messages.parse` signature |
| Response parsing | Verified through the SDK's own `parse_response`, the same function the real client runs |
| Failure translation | Verified for every documented error class |

## Safety

The prompt forbids health, ability and identity claims, and the product carries
the standing disclaimer:

> This is an automated analysis of one recording. It is not a professional,
> clinical or educational assessment.

A mock result must never be presented as genuine analysis — provenance carries
`is_mock` for exactly that reason, and the UI renders a prominent banner rather
than a footnote.

**A model is never asked to interpret a recording with nothing to interpret.**
An audio analysis that failed with `INSUFFICIENT_PITCH_SIGNAL` — ordinary
speech, a whisper, a noisy room — is refused at the service boundary before any
provider is constructed. That is what stops a vocal assessment being invented
from a recording that contained no singing.

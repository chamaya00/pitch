# AI interpretation layer

> **Status: adapter implemented (Step 7C), never executed against the live API.**
> This environment has no Anthropic credentials, so no request from this adapter
> has been answered. See *Verification status* below.

## Role

The LLM explains measurements. It does not produce them.

| Deterministic analysis | Claude |
| --- | --- |
| Word count, speaking duration | Plain-language explanation |
| Speaking rate, articulation rate | What the pace means for a listener |
| Pause count and durations | Pattern description |
| Filler-word counts, when measurable | Practical suggestions |

If a number appears in the UI it came from `services/analysis/metrics.py`. The
prompt supplies an already-computed payload, and the response is validated
before anything is stored or shown.

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
| Live Claude request | **Not verified** — no credentials in this environment (`api.anthropic.com` is reachable and answers 401) |
| Request construction | Verified against the installed SDK's `messages.parse` signature |
| Response parsing | Verified through the SDK's own `parse_response`, the same function the real client runs |
| Failure translation | Verified for every documented error class |

## Safety

The prompt forbids health, ability and identity claims, and the product carries
the standing disclaimer:

> This is an automated analysis of one recording. It is not a professional,
> clinical or educational assessment.

A mock result must never be presented as genuine analysis — provenance carries
`is_mock` for exactly that reason.

# Audio analysis

> **Status.** Two things live under this heading, both implemented, and they
> must not be confused with each other.
>
> - **Live pitch in the browser (Step 7H)** — real-time, local, latency-bound.
> - **Backend analysis of uploaded recordings (Step 7I)** — offline, over the
>   whole saved file, with more measurements and stricter aggregation.
>
> They share the musical reference below (A4 = 440 Hz) and nothing else: a
> different frame length, a different clarity threshold, a different smoothing
> rule, and — crucially — different definitions for range and stability. **Their
> numbers are not comparable and are never presented side by side.** The live
> readout is labelled "Live recording estimate" wherever it appears; the backend
> result is labelled "Audio analysis".

## Principles

1. Every reported number is computed here, deterministically, from the signal.
2. Measurements are reported as measurements. Subjective labels ("bright",
   "breathy") are not derived from a single spectral number without a validated
   classification method behind them.
3. Failure to analyse is a normal outcome with a clear error code, not a crash.

## Implemented: live pitch in the browser

Real-time pitch detection for the microphone recorder. It runs entirely in the
page — **microphone audio is never sent to Deepgram, to Claude, or to the
VocalLens API while recording.** The only thing that can be uploaded is the
finished WAV, and only when the person presses the button.

### Signal path

```
getUserMedia ─▶ MediaStreamAudioSourceNode ─▶ AudioWorklet ──postMessage──▶ main thread
                                              (capture only)                  │
                                                              detectPitch ◀───┤
                                                                   │          │
                                                              PitchStream     └──▶ PCM blocks
                                                                   │                   │
                                                                   ▼                   ▼
                                                              live readout        WAV on stop
```

There is exactly one copy of the audio. The worklet
(`frontend/public/pcm-capture-worklet.js`) analyses nothing; it batches frames
and hands them over. Detection runs on the main thread over the same samples
that are written to the WAV, so **the recording you play back is the recording
that was measured**.

`MediaRecorder` is deliberately not used: it produces WebM/Opus (or MP4/AAC on
Safari), and the backend accepts WAV and MP3 *by content*. Raw PCM plus our own
header (`lib/wav.ts`) keeps one format end to end and needed no server change.

### Actual parameters

| Parameter | Value | Rationale |
| --- | --- | --- |
| Sample rate | device native (48 kHz typical) | Whatever `AudioContext` gives us; never resampled, so nothing is lost before measurement |
| Capture batch | 2048 frames (worklet → main) | 128-frame messages are ~375/s and stutter the display; 2048 is ~23/s |
| Detection window | 2048 samples (~43 ms @ 48 kHz) | Holds two periods down to ~47 Hz, still short enough to track a voice |
| Detection rate | ≤ 30 Hz (`DETECTION_INTERVAL_MS = 33`) | Faster is invisible; slower reads as lag |
| Search range | 60–1200 Hz | Below/above this is not a voice worth reporting |
| Clarity threshold | 0.90 | Deliberately strict — see "Voicing" |
| Silence gate | RMS < 0.005 | Skips the arithmetic entirely on silence |
| Peak acceptance | 0.85 × global max | The octave fix — see below |
| Smoothing | median of last 5 voiced frames | ~165 ms; a mean would follow an outlier |
| Unvoiced hold | 3 frames (~100 ms) | A consonant should not blank the display |
| History cap | 20 000 frames | ~11 minutes, longer than the server's limit, and bounded |
| Gain processing | `echoCancellation`, `noiseSuppression`, `autoGainControl` **all off** | AGC rewrites level; noise suppression is spectral and moves pitch |

### Detector: NSDF (McLeod), not autocorrelation

`frontend/lib/pitch-detector.ts` computes the **normalised square difference
function**:

```
nsdf[lag] = 2 · Σ x[i]·x[i+lag] / Σ (x[i]² + x[i+lag]²)
```

then takes the **first** local peak reaching 0.85 of the global maximum, and
refines it by parabolic interpolation through its three neighbouring points.
`frequency = sampleRate / refinedLag`.

**What didn't work: plain autocorrelation.** Its peaks grow at integer multiples
of the true period, so a harmonically rich signal — which is what a voice is —
correlates at least as strongly at twice the period as at the true one. Taking
the tallest peak therefore reports an octave too low, intermittently, which
looks like the singer jumping octaves. Two changes fix it, and both are load-
bearing:

1. **Normalising** each lag by the energy actually overlapping at that lag,
   which removes the systematic bias towards longer lags.
2. **Taking the first acceptable peak** rather than the tallest. This is the
   part that actually prevents the octave error; normalisation alone still
   leaves ties.

`tests/pitch.test.ts` pins this: synthetic tones with five harmonics at 110,
165, 220 and 330 Hz must each be detected within 15 cents of the fundamental.

Parabolic interpolation matters more than it looks. Without it the period is
quantised to whole samples; at 48 kHz in the vocal range that is worth tens of
cents, which makes the cents readout meaningless.

### Voicing

A detector always emits *some* lag. Silence, a chair or a consonant will
produce a confident-looking number, so the clarity value gates the display:
below 0.90 the frame is marked unvoiced and **no note is shown at all**. The
threshold is strict on purpose — a wrong note shown confidently is worse than
no note.

The clarity figure is a measurement (the normalised peak height, 0–1), not an
invented confidence score.

### Note breakdown

`backend/app/services/audio_analysis/notes.py`. A **pure aggregation of the
pitch timeline** — it decodes nothing, detects nothing and re-measures nothing.
Every frame it sees has already passed the clarity gate, the octave-outlier
rejection and the note conversion. There is one pitch detector in this system
and it is upstream of here.

It answers one question: *how much of the pitched time went to each note?*

**Duration is counted in hops, not frames.** Analysis frames overlap — 2048
samples advancing 512 at a time — so charging each frame its full length would
count the overlap four times over and report durations several times longer than
the recording. Each frame is charged one hop, the audio it newly brought:

```
duration_seconds = frame_count × (hop_length_samples / sample_rate_hz)
```

which makes the note durations sum to the voiced time exactly.

**Voiced time is the denominator, never the recording's duration.** Unvoiced
audio never reaches the timeline, so voiced time is simply `frame count × hop`:

```
percentage_of_voiced_time = 100 × note_frames / total_voiced_frames
```

A recording that is half silence would otherwise report every note at half its
real share and the shares would sum to 50. As defined, they sum to 100.

**A note is a semitone, not a frequency.** Frames group by nearest MIDI note, so
C4 at −12, −5, +4 and +8 cents is one entry for C4 — not four. How far those
frames sat from it is what `average_cents` (signed) and `mean_abs_cents`
(absolute) report, and `in_tune_ratio` is the share within
[`IN_TUNE_CENTS`](#aggregates-as-implemented) — the same 25-cent threshold the
recording-level figure uses, imported from the domain rather than restated so
the two cannot drift.

**No minimum duration is applied.** This is deliberate and it is the one place
the feature could have quietly lied. Transient artefacts were already removed
upstream by rules with tests behind them; adding a second threshold here would
have silently deleted short notes that are real. A passing note held for two
frames appears with `frame_count: 2` and a share near zero, which states how
thin the evidence is rather than hiding it. On the two-tone test fixture this
shows up as a single `A3` frame at the boundary between A4 and C4 — one frame,
0.02 s, 0% — visible in the breakdown and correctly excluded from the range.

**Ordering is total.** Longest first, lower note first on a tie. Without the
tie-break the order would follow dictionary iteration and the same analysis
could render differently between runs.

**The range is not affected.** The breakdown is a separate view over the same
timeline; `_sustained` and `_vocal_range` are untouched, and the range remains
the authoritative one.

### AI interpretation (Step 7L)

A completed audio analysis can optionally be explained in plain language. The
architecture is the same one the speech half uses and the boundary is the point
of it:

```
audio → deterministic analysis → structured measurements → model → prose
```

**The model never sees the audio**, and never performs the first arrow. Every
number in the prose came from this pipeline. What it is given, what is withheld
and what the prompt forbids are documented in [ai.md](ai.md).

Two guards live on the audio side specifically. A recording whose analysis
failed with `INSUFFICIENT_PITCH_SIGNAL` is refused before a provider is
constructed — ordinary speech must not come back as a vocal assessment — and
the payload builder omits unavailable measurements entirely rather than sending
`null`, so an absent value cannot be filled in or read as zero.

### Conversions and smoothing

Conversions live in `frontend/lib/pitch.ts` and use the same reference as the
backend (A4 = 440 Hz, MIDI 69) — see "Conversions" below.
MIDI is kept **fractional**: rounding before measuring the cents deviation
would throw away the thing being measured. Anything that cannot be a pitch —
0 Hz, a negative, `NaN`, `Infinity`, `null`, or a frequency outside 20–5000 Hz
— returns `null` rather than a note.

Smoothing (`frontend/lib/pitch-stream.ts`) is a **median** of the last five
voiced frames, not a mean, so a single octave-jumped frame cannot move the
display. The window is deliberately short: longer looks calmer and starts to
lag visibly behind the voice, which for a live readout is the worse failure.

### Live Vocal Practice (Step 7J)

The practice card reads the sample stream described above and adds no detection
of its own. `frontend/lib/live-practice.ts` is arithmetic over
`LivePitchSample`; the detector, the clarity gate and the median smoother are
untouched.

**Pitch meter.** The needle position is `clamp(cents, ±50) / 50`. The *display*
is clamped and the *number* is not: past ±50 cents the next semitone is nearer,
so the needle stops at the end while the text keeps reporting the true
deviation and marks that the needle is pinned. A needle resting at the end must
never be read as "exactly 50 cents sharp".

Direction is carried as text ("23 cents flat"), as a `data-direction`
attribute, and only then as a tint — colour is never the sole cue.

**Live consistency.**

```
consistency = (voiced frames in the last 128 within ±25 cents of the nearest note)
              ─────────────────────────────────────────────────────────────────
                            (voiced frames in the last 128)
```

Roughly the last four seconds at ~30 frames a second. Reported only once **30
voiced frames** exist; below that it reads "Not enough yet", which is a
different statement from 0% and is never rendered as one. Unvoiced frames never
enter the window, so silence cannot lower it. It is a measure of how close the
pitch sat to *some* note — **not of singing ability**, and there is no reference
melody to be right or wrong against.

**Session range.** The same held-pitch rule the backend applies to a saved
recording: a pitch must hold for **5 consecutive voiced frames within 1
semitone** (~165 ms) before it counts. A run is broken by any unvoiced frame, so
a "held" pitch either side of silence is two pitches. This is what stops a
two-frame transient between notes from becoming the bottom of a reported range.
Labelled *range detected in this session*, never a physiological range.

**Target note (optional mode).** Choosing a target changes what the deviation is
measured *from* — the target note rather than the nearest one. The guarantee it
exists to keep: **a detected pitch is not a correct pitch.** Singing B3 against a
C4 target reports `B3, 1 semitone below the target`, not "C4, slightly flat", and
an octave error reports as an octave. `onTarget` is true only when the nearest
note to the sung pitch *is* the target.

**Render budget.** Note, cents, frequency, needle and trace are written to the
DOM from the subscription and never through React. The only state is the target
note (a click) and the session summary, published on a 500 ms timer — two
renders a second rather than thirty. Measured in Chromium over a live session:
~14% of wall time in tasks, ~7% scripting, ~1% layout.

**Accessibility.** No `aria-live` region updates at frame rate; the only polite
regions carry lifecycle text ("Recording."). The meter is a `role="img"` with a
static description, flat/sharp is text, and the target picker is a native
`<select>`.

### What the live summary is, and is not

`LiveSummary` reports the lowest, highest and most-held note, the mean cents
deviation, and the share of frames that were voiced. It is labelled **"Live
recording estimate"** everywhere it appears, and the UI states that it is not
the speech analysis. Detected range is what the recording contained, never a
physiological maximum.

## Implemented: backend analysis of uploaded recordings

`backend/app/services/audio_analysis/`. Runs on the stored file after upload,
in the background, and produces the measurements the API exposes at
`/recordings/{id}/audio-analysis`.

```
stored file → streamed frames → per-frame pitch + features
            → clarity gate → outlier rejection → held-pitch filter
            → range · stability · loudness · spectrum
```

### Decoding and preprocessing

- **`soundfile` (libsndfile)** reads WAV and MP3 — the two formats upload
  accepts — with no ffmpeg process and no temporary files.
- Channels are averaged to mono. A stereo recording of one voice is one voice.
- **No resampling.** Analysis runs at the file's own sample rate. A resampler is
  a dependency and a second source of error, and pitch detection gains nothing
  from discarding samples: a higher rate gives *finer* lag resolution. Frame and
  hop are therefore defined in **seconds** and converted per file, so the time
  resolution of a result is the same whatever the recording's rate.
- Amplitude is **not** normalised. Normalising before measuring RMS and peak
  would make both meaningless.
- Frames arrive from `soundfile.blocks` one at a time with the overlap built in.
  A 50 MB recording is never held in memory as floats.

### Actual parameters

| Parameter | Value | Rationale |
| --- | --- | --- |
| Sample rate | the file's own | No resampler; see above |
| Frame length | 0.0929 s (2048 samples @ 22.05 kHz) | Holds two periods of 65 Hz with room to spare |
| Hop | 0.0232 s (~43 frames/s) | A quarter of the frame; finer buys resolution nothing uses and multiplies the timeline |
| `fmin` | 65 Hz | Below typical bass range, above most room rumble |
| `fmax` | 1100 Hz | Above typical soprano range |
| Clarity threshold | **0.80** | Measured against noise — see below |
| Silence gate | RMS < 0.005 | Skips the arithmetic on silent frames |
| Peak acceptance | 0.85 × global max | The octave fix |
| Outlier rejection | > 6 semitones from a 5-frame median | Removes transient artefacts |
| Held-pitch rule | ≥ 5 consecutive frames within 1 semitone | Range only |
| In-tune threshold | 25 cents | Definition of `in_tune_ratio` |
| Minimum duration | 0.25 s | Below this there is no usable frame |

**The clarity threshold was chosen against evidence, not taste.** On a
five-harmonic 196 Hz tone with added noise, 0.85 and 0.90 both rejected *every*
frame at 12 dB SNR — a realistic room — while 0.80 kept 98% of them. The strict
values looked safer and would have made the feature useless on real recordings.

### Detector: NSDF (McLeod), not pyin

The same algorithm as the browser, and for the same reason — see
["Detector: NSDF (McLeod), not autocorrelation"](#detector-nsdf-mcleod-not-autocorrelation)
above for why plain autocorrelation reports a voice an octave low.

**What was planned and is not used: `librosa.pyin`.** The reason is dependency
weight, not quality: librosa pulls in numba, scikit-learn and soxr for one
function, where what is actually needed is a decoder and ~200 lines of numpy —
and the browser half of the product had to implement the detector anyway, so
using the same method means one mathematical contract instead of two. The
autocorrelation numerator is computed by FFT rather than a double loop; the
direct form is O(n²) per frame, which at a few thousand frames is the difference
between seconds and minutes.

pyin or CREPE get reconsidered when a real recording defeats this, with the
failure case written down first. Not on a hunch.

### Voiced/unvoiced handling

A frame is voiced when the detector resolves a frequency in range **and** the
measured clarity reaches the threshold. Everything else is unvoiced and
contributes nothing but a denominator.

**Unvoiced frames are omitted from the timeline, not stored as nulls.** Two
reasons: a point that exists is a point that was measured, so nothing has to
filter before plotting; and `stability.voiced_ratio` already states exactly how
much was left out. A gap between consecutive timestamps therefore means silence
— the UI draws it as a gap and never interpolates across it.

### What didn't work: raw min/max for the range

The first implementation took the extremes of every voiced frame. On a test
signal of exactly two notes — 440 Hz then 261.6 Hz — it reported **F2 to A4, a
span of 28 semitones**, where the truth is C4 to A4 and 9.

The cause was a single frame straddling the boundary between the two notes: it
contains both, and resolved to a sub-harmonic. One frame in 255. A range is
exactly the statistic where one bad frame does maximum damage.

Two rules fixed it, and both are load-bearing:

1. **Outlier rejection.** A frame further than 6 semitones from the median of
   its five-frame neighbourhood is discarded. A median, not a mean, so the
   outlier being tested cannot drag its own reference. This brought the range to
   A3–A4.
2. **The held-pitch rule.** Only frames in a run of ≥ 5 consecutive frames
   staying within 1 semitone (~116 ms) contribute to the range. The reasoning is
   musical rather than statistical: a frequency touched for 23 ms while crossing
   between two notes is not a note anyone sang. This brought the range to C4–A4,
   which is correct.

A percentile bound was the documented plan and was rejected: it hides a genuine
low note in a short recording, where these two rules remove the artefacts a
percentile was meant to hide without discarding real content.

Both rules apply to the **range only**. The timeline keeps every voiced frame at
the frequency it was measured at, including the transient — so the graph shows
what happened and the range describes what was sung.

### Aggregates, as implemented

- **Detected range** — lowest and highest held pitch, as frequency, note and
  whole-semitone span. This is the range *in this recording*, never a
  physiological limit.
- **Pitch stability** — voiced ratio, signed and absolute mean cents deviation,
  cents standard deviation, semitone variance, `in_tune_ratio` (share of voiced
  frames within 25 cents), and unstable sections. A section is a run of ≥ 0.25 s
  whose rolling 5-frame cents standard deviation exceeds 35 cents; runs are
  broken by gaps, so a section never spans silence. **None of these is a skill
  score.**
- **Loudness** — RMS, peak, crest factor, clipped-sample ratio, and a dynamic
  range *estimate* (95th minus 5th percentile of per-frame RMS, in dB —
  percentiles rather than max-minus-min so one clipped sample cannot define it).
  These are **not LUFS**: no loudness weighting, no gating, no reference level.
- **Spectral** — centroid, bandwidth, 85% rolloff, zero-crossing rate and
  flatness, averaged over frames above the silence gate. Reported as raw
  measurable characteristics. **No timbre label is derived from them anywhere**
  — "bright", "dark", "breathy", "nasal" are classifications, and no validated
  classifier exists in this project.

Every one of these is `null` when the signal could not support it. A recording
with nothing voiced has no range and no deviation; it does not have a range of
zero semitones.

### Note breakdown

`backend/app/services/audio_analysis/notes.py`. A **pure aggregation of the
pitch timeline** — it decodes nothing, detects nothing and re-measures nothing.
Every frame it sees has already passed the clarity gate, the octave-outlier
rejection and the note conversion. There is one pitch detector in this system
and it is upstream of here.

It answers one question: *how much of the pitched time went to each note?*

**Duration is counted in hops, not frames.** Analysis frames overlap — 2048
samples advancing 512 at a time — so charging each frame its full length would
count the overlap four times over and report durations several times longer than
the recording. Each frame is charged one hop, the audio it newly brought:

```
duration_seconds = frame_count × (hop_length_samples / sample_rate_hz)
```

which makes the note durations sum to the voiced time exactly.

**Voiced time is the denominator, never the recording's duration.** Unvoiced
audio never reaches the timeline, so voiced time is simply `frame count × hop`:

```
percentage_of_voiced_time = 100 × note_frames / total_voiced_frames
```

A recording that is half silence would otherwise report every note at half its
real share and the shares would sum to 50. As defined, they sum to 100.

**A note is a semitone, not a frequency.** Frames group by nearest MIDI note, so
C4 at −12, −5, +4 and +8 cents is one entry for C4 — not four. How far those
frames sat from it is what `average_cents` (signed) and `mean_abs_cents`
(absolute) report, and `in_tune_ratio` is the share within
[`IN_TUNE_CENTS`](#aggregates-as-implemented) — the same 25-cent threshold the
recording-level figure uses, imported from the domain rather than restated so
the two cannot drift.

**No minimum duration is applied.** This is deliberate and it is the one place
the feature could have quietly lied. Transient artefacts were already removed
upstream by rules with tests behind them; adding a second threshold here would
have silently deleted short notes that are real. A passing note held for two
frames appears with `frame_count: 2` and a share near zero, which states how
thin the evidence is rather than hiding it. On the two-tone test fixture this
shows up as a single `A3` frame at the boundary between A4 and C4 — one frame,
0.02 s, 0% — visible in the breakdown and correctly excluded from the range.

**Ordering is total.** Longest first, lower note first on a tie. Without the
tie-break the order would follow dictionary iteration and the same analysis
could render differently between runs.

**The range is not affected.** The breakdown is a separate view over the same
timeline; `_sustained` and `_vocal_range` are untouched, and the range remains
the authoritative one.

### AI interpretation (Step 7L)

A completed audio analysis can optionally be explained in plain language. The
architecture is the same one the speech half uses and the boundary is the point
of it:

```
audio → deterministic analysis → structured measurements → model → prose
```

**The model never sees the audio**, and never performs the first arrow. Every
number in the prose came from this pipeline. What it is given, what is withheld
and what the prompt forbids are documented in [ai.md](ai.md).

Two guards live on the audio side specifically. A recording whose analysis
failed with `INSUFFICIENT_PITCH_SIGNAL` is refused before a provider is
constructed — ordinary speech must not come back as a vocal assessment — and
the payload builder omits unavailable measurements entirely rather than sending
`null`, so an absent value cannot be filled in or read as zero.

### Conversions

Reference: **A4 = 440 Hz**, MIDI 69.

```
midi   = 69 + 12 * log2(f / 440)
cents  = 100 * (midi - round(midi))        # deviation from nearest semitone
note   = NOTE_NAMES[round(midi) % 12]
octave = round(midi) // 12 - 1             # MIDI 60 → C4
```

Checks: 440 Hz → A4 (MIDI 69, 0 cents); 261.626 Hz → C4 (MIDI 60, 0 cents).
Tests use tolerances, never floating-point equality.

**This is the one contract the two implementations share.** It is written down
in `backend/app/services/audio_analysis/pitch.py` and implemented again in
`frontend/lib/pitch.ts`, because the live display needs it at frame rate in the
page. The implementations are separate on purpose and the mathematics is not;
the same reference cases are asserted in `tests/test_audio_pitch_math.py` and in
`frontend/tests/pitch.test.ts`.

Both keep MIDI **fractional** until the last moment — the cents deviation *is*
the distance to the nearest semitone, and rounding first throws away the
measurement — and both return nothing for a value that cannot be a pitch: zero,
a negative, a NaN, an infinity, or a frequency outside 20–5000 Hz.

## Musical key (Phase 8)

`backend/app/services/audio_analysis/key.py`, reachable through
`AudioAnalysisService.key()`. A third aggregation of the same stored pitch
timeline, beside the note breakdown: it folds the timeline into twelve
pitch-class shares and reports which key those shares best fit, or a stated
reason there is none.

```
pitch timeline ─▶ pitch-class profile ─▶ 24 correlations ─▶ key | None
```

Served at `GET /recordings/{id}/audio-analysis/key` — see [api.md](api.md) — and
rendered by `frontend/components/audio-analysis/musical-key-card.tsx`, below the
note breakdown it shares a timeline with. It is not in the AI payload, the
comparison or the progress series, and those remain deliberately out of scope.

**The card shows the evidence in every state, including the one with no key.**
That is the obligation attached to shipping a classification rather than a
measurement: a reader told "not measured" can see the twelve pitch-class shares
that led there, which is the difference between a refusal and a shrug.

One threshold lives only in the frontend, `WEAK_KEY_CONFIDENCE = 0.19` in
`lib/audio-analysis-metrics.ts`. It is **presentational and never a second
refusal**: the backend's `MIN_KEY_CONFIDENCE = 0.05` decides whether a key is
reported at all, and this decides only whether an answered key is shown with its
weakness stated in words. It is set from the same sweep, at the bottom of the
band a *weighted* melody reaches:

| Input | Margin | Presented as |
| --- | --- | --- |
| Sung major melody | 0.262 | A key |
| Pentatonic melody | 0.241 | A key |
| Sung minor melody | 0.205 | A key |
| Bare unweighted major scale | 0.146 | A key, on thin evidence |
| Below the backend gate | < 0.05 | Not measured, with a reason |

The bare scale is the case that fixes the line. The specification requires it be
"answered, and answered weakly", and a synthesised one measured end to end
through the real decoder and analyzer scores **0.147 with A minor as its
runner-up** — its own relative minor, which is exactly the ambiguity the number
is reporting.

It differs from the note breakdown in exactly one way, and that difference is the
whole feature: **the octave is discarded.** `A2`, `A4` and `A5` are three pitches
and one pitch class. Everything else is shared — the same timeline, the same
hop-weighted durations, the same "derived on read, never persisted" discipline,
and the same single pitch detector upstream of both. Deriving rather than storing
means every analysis ever completed is answerable, and no migration exists.

Three properties, and the third is why the feature is safe to show at all:

- **Confidence is a margin, never a correlation.** Random pitch-class weights
  correlate +0.492 with a real key profile and a single held note +0.484 — under
  Krumhansl–Kessler the hum reaches **+0.684**, which reads as certainty. What
  separates music from arithmetic is how far the best candidate stands clear of
  the next: 0.262 for a sung melody against 0.013 for noise.
- **The margin is over the next candidate of any kind**, not the next candidate
  with a different tonic. Two hummed pitch classes lead the next *tonic* by 0.173
  — indistinguishable from music — and the next *candidate* by 0.021.
- **Two evidence gates sit in front of the correlation**, because a margin alone
  is not enough: a three-class arpeggio scores **0.309**, *higher than real
  music*, and only a distinct-pitch-class count catches it.

### The profile set, and the one that lost

Two published key-profile sets were run against the whole fixture set before
either was adopted, and neither was chosen on reputation. Both identify **all 24**
synthetic keys correctly, so accuracy did not decide it. What decided it is the
gap between the inputs that must be answered and the inputs that must not:

| Margin | Krumhansl–Kessler | Temperley |
| --- | --- | --- |
| Sung major melody | 0.246 | 0.262 |
| Sung minor melody | 0.276 | 0.205 |
| Chromatic wander | 0.072 | 0.010 |
| Random weights | 0.088 | 0.013 |

**Krumhansl–Kessler** (1982) comes from probe-tone listening experiments — what
listeners reported. **Temperley** (2001) is derived from the Kostka–Payne corpus
— what music actually contains. Krumhansl–Kessler separates music from noise by
about 3×; Temperley by about 12×. The wider band is what lets the confidence gate
sit clear of the noise floor without discarding real melodies, so Temperley ships
as `DEFAULT_PROFILE_SET`. Krumhansl–Kessler is kept in `PROFILE_SETS` and stays
under test; a result names the set it used.

### Thresholds, and the measurements that set them

Every threshold was left unset until the fixtures existed, then swept. All four
live beside their measurements in `key.py`:

| Constant | Value | What set it |
| --- | --- | --- |
| `MIN_DISTINCT_PITCH_CLASSES` | 5 | The smallest value admitting a pentatonic melody (0.241) while refusing a four-class arpeggio (0.171) |
| `MIN_VOICED_SECONDS` | 1.0 | Derived, not picked: five held notes at the analyzer's own 116 ms rule is ~0.58 s, rounded up |
| `MIN_KEY_CONFIDENCE` | 0.05 | The noise floor — uniform 0.000, chromatic 0.010, random 0.013, worst refusable fixture 0.022 |
| `MIN_PITCH_CLASS_SHARE` | 2.0 % | A stray frame in a two-second recording is worth ~1.2 % of voiced time; the least-used class of a real seven-note melody is several times that |

`MIN_VOICED_SECONDS` earns its place independently: a five-class fixture only
0.53 s long scores 0.281 and passes every other gate. `MIN_KEY_CONFIDENCE` sits
in the empty band between noise and music — real melodies jittered ±50 % on every
degree (200 draws each, tonic and mode correct in 599 of 600) have 5th-percentile
margins of 0.097 major, 0.081 pentatonic, 0.046 minor.

**The tails overlap, and the gate does not hide it.** One jittered minor melody
in 200 scored 0.004 — below the chromatic fixture. No threshold separates those
two. A feature whose honest answer is sometimes "not measured" is the specified
behaviour rather than a shortfall.

### What didn't work

The three properties above are each stated against an alternative that was tried
and measured first. Recorded because each rejected design is the one somebody
reaches for next, and only the measurement argues against it:

- **Correlation as confidence** — the obvious implementation, refuted by the
  +0.484 and +0.684 above. Height carries no information about whether a key is
  there; only distance from the next candidate does.
- **A margin over the next candidate with a *different tonic*** — the
  natural-looking refinement, refuted by the hum leading the next tonic by 0.173.
  This is the one that **survived the first mutation run**: every adversarial
  fixture is stopped by the pitch-class gate before the correlation is reached,
  so none of them could see how the margin was defined, and the property the
  module is built on was untested. `AMBIGUOUS_MODE` closes it — a melody singing
  both thirds, eight pitch classes over 3.8 s, where C minor (0.8197) leads C
  major (0.7992) by 0.0205 over the next candidate and 0.4094 over the next
  different tonic. Under the weaker definition that is a confident C minor; under
  the shipped one it is refused, which is the honest answer for a recording that
  did not choose a mode.
- **Confidence as the only gate** — refuted by the arpeggio's 0.309, and the
  reason two evidence gates exist at all.
- **Dropping negligible pitch classes from the profile.** They are excluded from
  the *evidence count* only; every measured frame stays in the shares, because
  the correlation should see the recording as it was.
- **Requiring a bare unweighted scale to return `null`.** Specified in 10.7 on a
  Krumhansl–Kessler measurement where it led by 0.044, level with noise. Under
  the profile set that shipped it leads by 0.146 — above the gate and below the
  weighted band, so it is answered, and answered weakly. Reporting a C major
  scale as C major is also what a listener would do.

### Cost

Folding is the whole expense; the 24 correlations are twelve-element arithmetic
at any recording length. Measured at the ceiling — 12 931 points, which is 300 s
at the analyzer's 23.2 ms hop, derived from `max_audio_duration_seconds` and
`HOP_SECONDS` rather than written down:

| Operation | Cost at the ceiling |
| --- | --- |
| `analyse_key` (fold + 24 correlations) | 1.35 ms |
| `estimate_key` alone, profile pre-folded | 0.18 ms |
| `summarise_notes`, the same timeline | 2.95 ms |

Those are development-machine timings, best of fifteen runs, and the tests do not
assert them. What is asserted is written to survive hardware nobody has measured:
a 5 ms bound about 3.5× the measurement, and three machine-independent ratios.

Key estimation is cheaper than the note breakdown this product has already been
paying synchronously on every `/notes` request since Step 7I, and peak allocation
is 7 216 bytes at the ceiling — the fold builds no copy of the timeline. Four
times the points cost under eight times the work, so raising the accepted
duration cannot quietly buy a quadratic. **No cache, queue, worker or background
task is warranted by any of this, and none was added.**

**The arithmetic was never the expensive part, and 10.11 measured how much it
was not.** Loading the stored analysis at the ceiling costs 83.8 ms and 19 MB of
peak allocation — 60× the fold it feeds. That is why `key_of` was given the
record the route already held rather than reading it again (slice 5), why
`notes_of` was given the same treatment in 10.11, and why the reads that return
no pitch point stopped loading a timeline at all: 1.8 ms and 15 kB instead —
though only since 10.12, which found that 10.11 shipped a second expression
over the same document and so was really paying 5.70 ms. The `/key` and
`/notes` endpoints still load the points, because they still fold them; nothing
about the algorithm changed. See [architecture.md](architecture.md).

**Step 10.14 measured what that load costs when more than one person asks at
once, and it is worse than the single-request figure suggests.** Building 12 931
`PitchPoint` models is ~50 ms of GIL-held work on the event loop, so the three
timeline endpoints serve ~7 requests a second between them however many clients
are waiting, and while one is running every other request in the process —
including the poll that says whether a measurement has finished — is stopped.
`/pitch` was fixed by asking PostgreSQL for the sample it returns instead of the
whole timeline; `/key` and `/notes` fold every point and therefore still load
every point, unchanged at ~135 ms. That is a property of the read, not of the
arithmetic: the folds are still 1.35 ms and 2.95 ms.

**Nothing here has been validated against human singing**: every fixture is
synthetic, and this repository holds no annotated corpus to do it with.

## Pitch accuracy

"Pitch accuracy" here means **consistency relative to the nearest equal-tempered
semitone** — the recording is judged against the note it appears to be aiming
for, because there is no reference melody in the MVP. It is presented as
*"pitch consistency in this recording"*, never as a singing-ability score.

Intentional pitch content (slides, vibrato, bends, non-Western intonation)
lowers this number without indicating a problem. That caveat belongs in the UI
next to the metric.

## Edge cases the pipeline must survive

Silence · near-silence · background noise · clipping · very short audio ·
unsupported formats · corrupted files · multiple simultaneous voices ·
instrumental-heavy mixes.

Each returns a documented error code rather than an exception escaping to the
client:

| Case | Code |
| --- | --- |
| Undecodable, truncated or missing file | `AUDIO_UNSUPPORTED` |
| Shorter than 0.25 s, or than one frame | `AUDIO_TOO_SHORT` |
| Decoded fine, no frame cleared the clarity gate | `INSUFFICIENT_PITCH_SIGNAL` |
| Anything else | `AUDIO_ANALYSIS_FAILED` |

`INSUFFICIENT_PITCH_SIGNAL` is a **normal outcome**, not a bug: a whisper, a
noisy room, a spoken monotone or an instrumental recording all produce it. The
UI shows it as "Not measured" rather than as an error.

Clipping and multiple simultaneous voices do not fail. Clipping is reported as a
ratio and the recording is measured anyway; a polyphonic recording produces a
pitch belonging to none of the voices, which is a documented limitation rather
than a detectable error.

Test fixtures cover silence, near-silence, white noise, a pure tone, harmonic
tones at eight fundamentals, clipped audio, a DC offset, four sample rates,
stereo, a very short clip, a truncated file and a missing file. A failure
message is asserted never to contain a filesystem path.

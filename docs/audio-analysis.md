# Audio analysis

> **Status.** Two separate things live under this heading, and they must not be
> confused with each other.
>
> - **Live pitch in the browser — implemented (Step 7H).** Real code, real
>   parameters, documented below.
> - **Backend audio analysis — design notes only.** No backend pitch code
>   exists. Everything from "Planned pipeline" onwards is a plan for Phase 2 and
>   must be updated with the *actual* parameters once that code lands.
>
> The two share a musical reference (A4 = 440 Hz) and nothing else. They do not
> share an algorithm, a sample rate, a frame size or a definition of any
> reported value, so their outputs are not comparable and are never presented
> side by side.

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

### Conversions and smoothing

Conversions live in `frontend/lib/pitch.ts` and use the same reference as the
planned backend pipeline (A4 = 440 Hz, MIDI 69) — see "Conversions" below.
MIDI is kept **fractional**: rounding before measuring the cents deviation
would throw away the thing being measured. Anything that cannot be a pitch —
0 Hz, a negative, `NaN`, `Infinity`, `null`, or a frequency outside 20–5000 Hz
— returns `null` rather than a note.

Smoothing (`frontend/lib/pitch-stream.ts`) is a **median** of the last five
voiced frames, not a mean, so a single octave-jumped frame cannot move the
display. The window is deliberately short: longer looks calmer and starts to
lag visibly behind the voice, which for a live readout is the worse failure.

### What the live summary is, and is not

`LiveSummary` reports the lowest, highest and most-held note, the mean cents
deviation, and the share of frames that were voiced. It is labelled **"Live
recording estimate"** everywhere it appears, and the UI states that it is not
the speech analysis. Detected range is what the recording contained, never a
physiological maximum.

## Planned pipeline

```
file → decode → mono → resample → trim/normalise → frame
     → pitch detection → confidence filter → Hz→MIDI→note→cents
     → aggregates (range, stability, loudness, spectral)
```

### Decoding and preprocessing

- Decode with `librosa.load` (soundfile/audioread backend).
- Convert to mono; stereo channels are averaged.
- Target sample rate: **22050 Hz** — comfortably above twice the highest
  fundamental a human voice produces (~1100 Hz for C6), and cheaper than 44.1k.
  Spectral features are computed at the same rate so the values stay comparable
  across recordings.
- Amplitude is *not* normalised before loudness measurement, or RMS and peak
  become meaningless.

### Pitch detection

Start with **`librosa.pyin`**:

- Probabilistic YIN; returns per-frame `f0`, a voiced flag and a voiced
  probability, which is exactly the confidence signal the UI needs.
- Pure Python/NumPy — no ML runtime, no model download, no GPU.
- Well suited to monophonic vocals, which is the MVP input.

CREPE is the fallback if pyin proves insufficient on real recordings. It is more
accurate on noisy input but pulls in TensorFlow and a model download, so it is
not the starting point. Any switch must be justified by a documented failure
case, per §36 of the specification.

Planned defaults (to be validated in Phase 2):

| Parameter | Value | Rationale |
| --- | --- | --- |
| `fmin` | 65 Hz (C2) | Below typical bass range, above most room rumble |
| `fmax` | 1050 Hz (C6) | Above typical soprano range |
| `frame_length` | 2048 samples (~93 ms @ 22050) | pyin needs several periods of the lowest expected pitch |
| `hop_length` | 256 samples (~11.6 ms) | ~86 pitch points/second — smooth graphs at reasonable cost |
| Confidence threshold | 0.5 voiced probability | Starting point; tune against real recordings and record the result |

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

### Aggregates

- **Vocal range** — lowest/highest note across frames passing the confidence
  filter, plus the span in semitones. Outlier frames (octave errors) must be
  handled; a percentile-based bound is likely more honest than a raw min/max,
  and whichever is chosen gets documented here.
- **Pitch stability** — voiced ratio, pitch variance, mean and standard
  deviation of cents deviation, and identification of unstable sections.
- **Loudness** — RMS and peak amplitude, plus an approximate dynamic range.
  These are *not* LUFS. Nothing in the UI may imply broadcast-standard loudness
  measurement unless a real LUFS implementation is added.
- **Spectral** — centroid, bandwidth, rolloff, zero-crossing rate, flatness.
  Reported as raw measurable characteristics.

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
client. Test fixtures will cover silence, a synthetic pure tone with a known
frequency, a very short clip, and a corrupted file.

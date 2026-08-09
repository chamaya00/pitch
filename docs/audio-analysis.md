# Audio analysis

> **Status: design notes only.** No analysis code exists yet (Phase 0). This
> document records the decisions Phase 2 will implement and must be updated with
> the *actual* parameters once code lands — including anything that turns out
> to work differently than planned.

## Principles

1. Every reported number is computed here, deterministically, from the signal.
2. Measurements are reported as measurements. Subjective labels ("bright",
   "breathy") are not derived from a single spectral number without a validated
   classification method behind them.
3. Failure to analyse is a normal outcome with a clear error code, not a crash.

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

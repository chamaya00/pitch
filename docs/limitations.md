# Limitations

What VocalLens can and cannot tell you. This page is user-facing in spirit: the
same caveats belong next to the numbers in the UI, not buried in a repository.

## Not a medical or professional assessment

VocalLens does not diagnose vocal disorders or any medical condition, does not
detect vocal damage, and does not provide medical recommendations. It measures
audio.

Standing disclaimer:

> This analysis is only an audio-based estimate and is not a medical or
> professional vocal assessment.

## Audio analysis of a saved recording

Everything VocalLens reports about pitch, range, stability, loudness and
spectrum is an **audio-based estimate from one recording**. Specifically:

- **It is monophonic.** One voice at a time. Two speakers, a voice over music,
  or an instrument in the room produce a pitch belonging to none of them, and
  nothing detects that this has happened.
- **It is not the live estimate**, and the two will not agree exactly. Different
  algorithm, different window, different thresholds, different definitions of
  range and stability. Neither validates the other.
- **The measurement depends on the recording**, not only the voice. Microphone,
  room, input level, distance and lossy compression all move these numbers.
- **`INSUFFICIENT_PITCH_SIGNAL` is common and normal.** A whisper, a noisy room,
  a spoken monotone or an instrumental recording will produce no reliable pitch,
  and the honest answer is "not measured".
- **Detection has limits of its own.** Frames at a note boundary can resolve to
  a sub-harmonic; the range guards against this with outlier rejection and a
  held-pitch rule, and the timeline still shows the raw measurement. Below 65 Hz
  and above 1100 Hz nothing is searched for at all.
- **Nothing here has been validated against reference pitch data.** It is an
  engineering MVP measured against synthetic signals with known fundamentals,
  not a system benchmarked on annotated recordings of real voices.

### Pitch consistency

`Pitch consistency` is the **share of voiced frames within 25 cents of the
nearest equal-tempered semitone**, and that definition travels with the number
everywhere it is shown. It is not a singing-ability score. There is no reference
melody, so it cannot say whether the note was the right one — only how close the
pitch sat to *some* note. Slides, vibrato, blues bends and non-Western
intonation systems all lower it without anything being wrong.

### Loudness and spectrum

RMS and peak are amplitude measurements, **not LUFS**: no loudness weighting, no
gating, no reference level. Dynamic range is an estimate from percentiles of
frame level, not a loudness range (LRA).

Spectral centroid, bandwidth, rolloff, zero-crossing rate and flatness are
reported as raw numbers. VocalLens does **not** turn them into words like
"bright", "dark", "breathy" or "nasal". Those are classifications and would need
a validated classifier trained on labelled data; none exists in this project.

## Live pitch (browser)

The live readout in the recorder is a **browser-side estimate**, labelled "Live
recording estimate" wherever it appears. Specifically:

- **It is not the speech analysis, and the two are not comparable.** They share
  a musical reference and nothing else — different algorithm, different window,
  different definitions. Neither one validates the other.
- **It is monophonic.** One voice at a time. Two people, a voice over music, or
  an instrument in the room will produce a pitch that belongs to none of them.
- **Silence and noise deliberately show nothing.** Frames below the clarity
  threshold display no note rather than a guess, so a quiet or noisy recording
  will show long gaps. That is the feature working, not failing.
- **It measures pitch, not singing.** It says which note the detector found, not
  whether that note was the right one — there is no reference melody.
- **It depends on the device.** Microphone quality, room noise and whatever the
  operating system does before the browser sees the signal all affect it. The
  browser's own processing (echo cancellation, noise suppression, automatic
  gain) is switched off, but nothing outside the browser can be.
- **Notes are spelled with sharps only.** Enharmonic spelling needs a key, which
  live audio does not supply; a sung D♭4 is displayed as C#4.
- **Range shown is what this recording contained**, exactly as below — never a
  limit of anyone's voice.

Microphone audio is never uploaded while recording, and no recording is sent
anywhere unless it is explicitly submitted for analysis.

## Vocal range

The reported range is the range **detected in this recording** — not a
physiological maximum. It is bounded by what was sung, the microphone, the room,
warm-up state, and the pitch detector's own `fmin`/`fmax` limits. Octave errors
in pitch detection can widen an apparent range artificially.

## Pitch accuracy

The MVP has no reference melody, so accuracy is measured as **consistency
relative to the nearest equal-tempered semitone**. This penalises things that
are not mistakes: intentional slides, vibrato, blues bends, and non-Western
intonation systems. The metric is presented as *pitch consistency in this
recording*, never as a measure of singing ability.

## Recording conditions

Results depend on the recording, not only the singer. Background noise, low
input level, clipping, compression artefacts from lossy formats and room
reverberation all affect detected pitch and every spectral measurement. Two
recordings are only comparable when captured under similar conditions — this
matters for the progress tracking in Phase 7.

## Loudness

RMS and peak amplitude are signal measurements relative to digital full scale.
They are **not** LUFS and carry no mastering or broadcast-standard meaning. They
also depend on input gain, so they say as much about the microphone setup as
about the singer.

## Timbre

Spectral centroid, bandwidth, rolloff, zero-crossing rate and flatness are
reported as measurable characteristics. VocalLens does not translate a single
one of them into a label such as "bright", "dark" or "breathy" — those mappings
are not validated, and the numbers are strongly influenced by the microphone and
the room.

## Songs and mixed audio (Phase 8+)

Pitch detection on a full mix is substantially less reliable than on an isolated
vocal. Instruments, harmonies and percussion all contribute energy that a
monophonic pitch detector may track instead of the voice. Any song-level result
is an estimate and is labelled as such.

## Song compatibility (Phase 9+)

A compatibility score compares a detected range against an estimated song range.
It is not an objective statement about whether someone can sing a song. Tessitura
(where a melody sits most of the time), breath demands, register transitions and
stylistic technique are not captured by range overlap.

## AI feedback

The feedback layer explains measurements; it never generates them. It can still
phrase things imprecisely or over-generalise from limited data. When measurements
are too sparse to support a conclusion, it is instructed to say so — treat its
output as coaching-style commentary, not fact.

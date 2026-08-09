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

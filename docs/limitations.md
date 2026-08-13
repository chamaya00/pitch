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

### Note breakdown

- **It describes detected pitch, not musical notation.** A note in the breakdown
  is "frames whose nearest semitone was this one", not a note anyone wrote down.
  Nothing here has been validated against annotated musical transcription, and
  no claim of note-recognition accuracy is made.
- **A slide leaves a trace on every note it passes through.** Moving from C4 to
  E4 produces small entries for C#4 and D4 — a true description of what the
  pitch did, and not a claim that those notes were sung deliberately.
- **Short entries are shown, not hidden.** No minimum-duration filter is
  applied; `frame_count` says how thin the evidence is. A single-frame entry is
  one 23 ms analysis window.
- **Percentages are of pitched time.** A recording that is mostly silence still
  reports shares summing to 100 — of the small part that carried a pitch. Read
  `voiced_ratio` alongside to know how much of the recording that was.
- **Monophonic, like everything else here.** Two voices produce a breakdown
  belonging to neither.

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

### Live Vocal Practice

- **Pitch consistency is not singing ability.** It is the share of the last ~4
  seconds of voiced audio that sat within 25 cents of the nearest note. With no
  reference melody it cannot say whether that note was the right one.
- **It says "Not enough yet", not 0%,** until 30 voiced frames exist. Those are
  different statements and are never rendered as the same thing.
- **Session range is what this session contained.** A pitch has to be held for
  ~165 ms to count, which excludes transients but also excludes genuinely fast
  passages.
- **Target-note mode does not grade you.** It reports the note you actually
  sang and its distance from the target. Singing a semitone or an octave away
  is reported as exactly that, never as the target note slightly out of tune.
- **The live figures will not match the audio analysis of the same recording.**
  Different window, different thresholds, different aggregation. Neither
  validates the other.

Microphone audio is never uploaded while recording, and no recording is sent
anywhere unless it is explicitly submitted for analysis.

### Not validated against real singing

Every part of this — live and offline — is an engineering MVP verified against
**synthetic signals with known fundamentals**. It is **monophonic**: one voice
at a time, with no detection that more than one is present. Nothing here has
been benchmarked against an annotated dataset of real singing, and no claim
about real-world accuracy is made or implied.

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

## Comparing two recordings

Comparison places two sets of measurements side by side and subtracts. It is
**not** a score, a ranking, or a statement about which recording is better —
there is no field in the response that could hold one.

- **Two recordings are only meaningfully comparable when captured under
  reasonably similar conditions.** VocalLens flags the differences it can
  actually measure — length, pitched time, sample rate, audio format, analysis
  settings, clipping — and that list is not exhaustive.
- **It cannot normalise for anything it does not measure.** Microphone quality,
  room acoustics, distance, how hard you were trying, warm-up state and physical
  condition all move these numbers, and none of them is measured or corrected
  for. A difference between two takes may be entirely about the room.
- **Most of the differences have no better direction.** Recording length,
  pitched time, voiced share and detected range are reported as differences and
  nothing more. A wider detected range is bounded by what was performed and by
  the microphone; it is not an achievement.
- **Only two comparisons have a defined desirable direction**, and both are
  about equal temperament rather than singing: a higher share of pitched time
  within 25 cents of a note, and a smaller typical distance from that note.
  Slides, vibrato, bends and non-Western intonation lower both without anything
  being wrong.
- **A measurement one recording supports and the other does not produces no
  difference at all** — not a difference against zero. A note present in one
  recording reads "Not sung" in the other, because it was not sung there.
- **Loudness and spectral measurements are not compared.** RMS and peak depend
  on input gain, so a difference says as much about the microphone setup as
  about the singer, and the spectral features have no validated interpretation
  here.
- **Two recordings, not a trend.** Comparison says how these two differ. It says
  nothing about a direction of travel, and there is no progress tracking in this
  product yet.

## Measurements over time

Progress tracking shows **your recorded measurements over time**. That is the
whole claim. It is not a level, a grade, a ranking, or evidence that anyone is
becoming a better singer.

- **Every point came from a recording made under conditions this system cannot
  measure.** Microphone, room, distance, effort, warm-up and physical condition
  all move these numbers, and none of them is measured or corrected for. A
  change between two points may be entirely about the room. There is no
  "condition score", because there is nothing to compute one from.
- **Four of the seven series have no better direction.** Detected range, voiced
  share, pitched time and recording length are described, never framed as
  improvement. A wider detected range is bounded by what was performed and by
  the microphone.
- **The three directed series are about equal temperament, not singing.** A
  higher share of pitched time within 25 cents of a note, a smaller typical
  distance from that note, and a smaller spread of placement. Slides, vibrato,
  bends and non-Western intonation move all three without anything being wrong.
- **No trend is calculated.** There is no slope, regression, moving average,
  percentage-improvement figure or forecast. The strongest statement made is how
  the latest measured value compares with the previous measured one.
- **Two points are not a trend**, and the product says so rather than letting a
  line between two dots imply otherwise. Three measured recordings is the
  minimum before a line is drawn as a line.
- **Unmeasured recordings are gaps, never zeroes.** A recording with no
  completed analysis, or one whose analysis completed without a particular
  measurement, contributes no point — and still appears in the table with the
  reason, because it belongs in its own history.
- **Loudness and spectral measurements are not plotted.** RMS and peak depend on
  input gain; the spectral features have no validated interpretation here; note
  count is not an achievement.
- **Comparable practice sessions would need controlled conditions** — the same
  microphone, room, distance and warm-up — which this product does not record
  and does not ask for. Until it does, the honest answer is to show the
  measurements with the caveat rather than to filter points by a condition model
  that does not exist.

## Recording history and identity

Recordings are linked to an **anonymous identifier stored in the browser**, not
to an account:

- **There is no login and no password.** Anyone holding a key is the owner of
  those recordings.
- **The server cannot recover a lost key.** Only a SHA-256 hash of it is
  stored, so there is no "forgot my key" and no reset. What the product does
  give you is the key itself, shown in the browser that holds it: save it and
  you can paste it on another device; lose every key without saving one and the
  history is unreachable for good.
- **You can hold several keys, and revoke one.** An identity can have more than
  one key — for another device, or a spare — each named for your own benefit.
  Revoking one removes a way in and no data. **This is still not authentication**:
  every key is a bearer credential, so revocation protects against a key you no
  longer want in circulation, not against somebody who already copied your
  recordings while holding it.
- **The last key cannot be revoked.** Removing it would leave the recordings
  owned and unreachable. Deleting the identity is the way to get rid of
  everything, and it is a different action.
- **You can delete everything.** "Delete everything" under *Your key* removes
  every recording, every measurement, every generated interpretation and the
  stored audio itself, irreversibly.
- **Clearing site data without saving the key still loses the history.** The old
  recordings remain on the server, unreachable, until they are deleted through
  an identity that can still reach them.
- **It is not a security boundary against a determined attacker.** The
  identifier is a 128-bit random value, which makes guessing one impractical,
  but the guarantee is the entropy — not an access-control system.
- **A take that is recorded but never submitted is never listed**, because it
  was never uploaded. It exists only in the browser tab.

What it *does* guarantee is that one visitor never sees another's recordings.
That check happens on the server, in the database query, on every route.

### Rate limits

Requests are limited: how many new identities one address may create, and how
many costly requests one identity may make. What that does **not** guarantee:

- **It is not a defence against a distributed attacker.** The identity limit is
  per address, so somebody with many addresses is bounded per address and not
  in total.
- **It is one process's memory.** A deployment running several API workers
  multiplies the effective limit by the number of workers. A shared limit would
  need a shared counter, which this deliberately does not have.
- **A shared address shares the newcomer allowance.** An office, a school or a
  phone network arriving for the first time all count against one bucket.
  Anyone who already has a key is unaffected — presenting a recognised key never
  touches that limit — but a genuine burst of *first* visits from one address
  can be refused.
- **It bounds the rate, not the total.** Reclaiming unused identities is Step
  10.6's job; see below.
- **Reading is never limited**, so it is not a defence against someone reading
  their own history repeatedly.

## When something breaks

The app has deliberate failure states, and they are honest about what they do
not know:

- **A failure state cannot tell you what went wrong.** It says a page failed and
  offers a retry, because the underlying error may contain a stack trace, a
  server path or a key, and none of that is shown. If a failure keeps
  happening, the detail is in the server's logs, not on the screen.
- **"Try again" is a genuine retry, not a fix.** It re-attempts the render. A
  failure with a persistent cause will simply happen again.
- **A wrong address is not an error.** The not-found page offers no retry
  because there is nothing to retry.
- **The global failure state is a last resort.** If the application shell itself
  fails, the page loses its header, footer and fonts — that is the point, since
  those are what failed. Recordings and keys are untouched by any of this;
  nothing is stored in the page.

## Reclaiming unused identities

An identity that owns **no recordings** and has not been used for the retention
period (30 days by default) is deleted, along with its keys. What that does
**not** cover:

- **Identities that own recordings are never deleted, at any age.** Nothing in
  this project specifies how long somebody's singing history should be kept, so
  nothing decides it. An identity abandoned five years ago with one recording in
  it stays forever.
- **Cleanup does not run by itself.** There is no scheduler; it is a command an
  operator runs. Nothing happens until somebody or something runs it.
- **"Last seen" is coarse.** It is refreshed at most once an hour, so it can lag
  real use by that much. That is far finer than a retention period measured in
  days, but it is not a precise record of activity.
- **Losing an empty identity is silent.** If yours is reclaimed you simply get a
  new one on your next visit, with no message — because there is nothing to tell
  you about. If you saved a key for an identity you never used, that key will
  eventually stop working.
- **Stored audio is not reclaimed by this**, because identities holding audio
  are never eligible. Orphaned files from a partly failed deletion are still
  reported by that deletion and are not swept up later.

## What the deployment does and does not protect

The bundled `docker-compose.yml` puts an nginx proxy in front of everything and
publishes nothing else. What that does **not** give you:

- **No TLS.** The proxy speaks HTTP. Terminating TLS — and therefore protecting
  the bearer key in transit — is an external responsibility, and the proxy
  deliberately advertises no HSTS rather than implying otherwise. **Over plain
  HTTP a key can be read by anyone on the network path.**
- **The rate limiter is still per process.** Running several API workers
  multiplies the effective limit by the worker count. The proxy does not add a
  shared counter and does not rate-limit itself.
- **The trust boundary is the network, not a check.** The backend trusts
  `X-Forwarded-For` because only the proxy can reach it. If a deployment
  republishes the backend's port, or runs it on a shared network, that
  assumption is void and the per-address limit becomes forgeable.
- **No Content-Security-Policy.** Adding one that is both useful and compatible
  with Next.js needs per-request nonces; a speculative one would be bypassable
  or would break the app.
- **The database password has a development default.** The database is not
  reachable from the host, but `POSTGRES_PASSWORD` should be set for anything
  real.
- **It is not a defence against a distributed attacker.** The proxy adds no
  connection limiting, no WAF and no bot detection.

## Songs and mixed audio (Phase 8+)

Pitch detection on a full mix is substantially less reliable than on an isolated
vocal. Instruments, harmonies and percussion all contribute energy that a
monophonic pitch detector may track instead of the voice. Any song-level result
is an estimate and is labelled as such.

The more basic limit, found by the Step 10.7 audit: **this product has no way to
receive a song.** The only accepted input is one uploaded recording of one voice
— there is no reference track, no catalogue and no vocal separation. What Phase 8
is specified to measure is therefore the key implied by *what was sung*, with its
own limits written down in
[phase-8-specification.md](phase-8-specification.md#l-what-phase-8-will-not-solve).
None of it is implemented.

## Song compatibility (Phase 9+)

A compatibility score compares a detected range against an estimated song range.
It is not an objective statement about whether someone can sing a song. Tessitura
(where a melody sits most of the time), breath demands, register transitions and
stylistic technique are not captured by range overlap.

## AI vocal feedback

The interpretation of the audio measurements is prose about numbers, and its
limits are the numbers' limits plus the model's:

- **It cannot hear the recording.** It is given measurements and nothing else,
  so it cannot notice anything the pipeline did not measure.
- **It cannot tell you your vocal range.** It may only describe the range
  detected in one recording. No single recording establishes a physiological
  limit.
- **It does not score you.** `in_tune_ratio` is reported with its definition
  attached; there is no field in the response that could hold an overall grade.
- **It does not describe your timbre.** The spectral measurements do not
  establish "bright", "dark", "breathy" or any other label, and the prompt
  forbids deriving one.
- **It is not a vocal-health or professional assessment**, and makes no claim
  about anatomy, technique quality or ability.
- **Demo output is marked.** With no provider configured the feedback comes
  from a development stand-in and says so in a banner.
- **A model can still be wrong.** It can phrase things imprecisely or
  over-generalise from thin data. The measurements above it are the record.

## AI feedback

The feedback layer explains measurements; it never generates them. It can still
phrase things imprecisely or over-generalise from limited data. When measurements
are too sparse to support a conclusion, it is instructed to say so — treat its
output as coaching-style commentary, not fact.

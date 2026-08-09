"""Deterministic speaking metrics computed from a transcript.

Every function here is pure: the same transcript in, the same numbers out. No
model is consulted, nothing is estimated, and no value is inferred from
anything other than the words and timings the transcript actually carries. This
module is the only thing that constructs :class:`SpeechMetrics`.

**Omission is the failure mode.** When the source data cannot support a metric,
the metric is ``None``. A transcript with no word timings has no pause count —
not zero pauses. A cleaned-up transcript has no filler statistics — not zero
fillers. The distinction matters because the UI will render these numbers as
facts about someone's speech.

Documented choices (see ``docs/speech-analysis.md``):

* A gap of ``DEFAULT_PAUSE_THRESHOLD_SECONDS`` (0.5 s) or more between two words
  counts as a pause. Below roughly 0.2 s a gap is ordinary articulation; 0.5 s
  is the conservative end of the 0.25–0.5 s range used for silent pauses in the
  speech-rate literature, chosen so that normal phrasing is not reported back to
  someone as hesitation.
* Speaking rate is reported in words per minute rather than syllables per
  second, because syllable counting needs a pronunciation dictionary we do not
  have and an English-only heuristic would be wrong on the first non-English
  recording.
* Articulation rate is the same word count over the same span with detected
  pauses removed. It is only available with word timings, since without them
  there are no pauses to remove.
"""

import re
from collections import Counter
from typing import Final

from app.services.analysis.models import (
    DurationSource,
    FillerCategory,
    FillerTermCount,
    FillerWordStats,
    Pause,
    SpeechMetrics,
    Transcript,
)

#: A silent gap at least this long is a pause. See the module docstring.
DEFAULT_PAUSE_THRESHOLD_SECONDS: Final = 0.5

_SECONDS_PER_MINUTE: Final = 60.0

#: Tokens are word characters plus internal apostrophes, so "don't" stays one
#: word and trailing punctuation never becomes part of it.
_TOKEN_PATTERN: Final = re.compile(r"[0-9A-Za-z]+(?:'[0-9A-Za-z]+)*")

#: Hesitation sounds and their spelling variants, mapped to a canonical term.
#: These have no meaning other than hesitation, so counting them is a
#: measurement rather than a guess about intent.
_HESITATIONS: Final[dict[str, str]] = {
    "um": "um",
    "umm": "um",
    "ummm": "um",
    "uh": "uh",
    "uhh": "uh",
    "uhhh": "uh",
    "er": "er",
    "err": "er",
    "erm": "erm",
    "ah": "ah",
    "ahh": "ah",
    "eh": "eh",
    "hm": "hmm",
    "hmm": "hmm",
    "hmmm": "hmm",
    "mm": "mmm",
    "mmm": "mmm",
}

#: Ordinary words that often act as fillers but frequently do not ("I like
#: this" is not hesitation). They are counted separately and must be presented
#: as a word-frequency count, never as a hesitation total. The list is
#: deliberately short: every entry added here is another chance to tell someone
#: they hesitated when they simply spoke.
_DISCOURSE_MARKERS: Final[dict[str, str]] = {
    "like": "like",
    "you know": "you know",
    "i mean": "I mean",
    "sort of": "sort of",
    "kind of": "kind of",
    "basically": "basically",
    "literally": "literally",
}

#: Longest phrase in the tables above, in tokens.
_MAX_PHRASE_TOKENS: Final = 2


def _round_seconds(value: float) -> float:
    return round(value, 3)


def _round_rate(value: float) -> float:
    return round(value, 1)


def tokenize(text: str) -> tuple[str, ...]:
    """Split text into lower-cased word tokens, discarding punctuation."""
    normalized = text.replace("’", "'")
    return tuple(match.group(0).lower() for match in _TOKEN_PATTERN.finditer(normalized))


def word_tokens(transcript: Transcript) -> tuple[str, ...]:
    """Normalised tokens for the transcript.

    Per-word data is preferred when the provider supplied it, because that is
    the provider's own segmentation; the full text is the fallback. A provider
    token that is pure punctuation contributes nothing.
    """
    if transcript.words:
        return tuple(token for word in transcript.words for token in tokenize(word.text))
    return tokenize(transcript.text)


def count_words(transcript: Transcript) -> int:
    """Number of spoken words. Always available, even from text alone."""
    return len(word_tokens(transcript))


def speaking_span_seconds(transcript: Transcript) -> float | None:
    """Seconds from the start of the first word to the end of the last.

    ``None`` unless every word is timed. Leading and trailing silence are
    excluded by construction — that is the point of using the words rather than
    the file duration.
    """
    if not transcript.has_word_timings:
        return None
    starts = [w.start_seconds for w in transcript.words if w.start_seconds is not None]
    ends = [w.end_seconds for w in transcript.words if w.end_seconds is not None]
    span = max(ends) - min(starts)
    return _round_seconds(span) if span > 0 else None


def find_pauses(
    transcript: Transcript,
    *,
    threshold_seconds: float = DEFAULT_PAUSE_THRESHOLD_SECONDS,
) -> tuple[Pause, ...] | None:
    """Gaps between consecutive words that reach ``threshold_seconds``.

    Returns ``None`` when the transcript has no word timings — there is nothing
    to detect, which is not the same as detecting nothing. An empty tuple means
    the timings were read and no gap was long enough.

    Words are read in timing order rather than list order, and overlapping words
    (some providers emit them) simply yield no gap.
    """
    if threshold_seconds <= 0:
        raise ValueError("pause threshold must be positive")
    if not transcript.has_word_timings:
        return None

    timings = sorted(
        (w.start_seconds, w.end_seconds)
        for w in transcript.words
        if w.start_seconds is not None and w.end_seconds is not None
    )

    pauses: list[Pause] = []
    previous_end = timings[0][1]
    for start, end in timings[1:]:
        if start - previous_end >= threshold_seconds:
            pauses.append(
                Pause(start_seconds=_round_seconds(previous_end), end_seconds=_round_seconds(start))
            )
        previous_end = max(previous_end, end)
    return tuple(pauses)


def total_pause_seconds(pauses: tuple[Pause, ...]) -> float:
    """Combined duration of the detected pauses."""
    return _round_seconds(sum(pause.duration_seconds for pause in pauses))


def words_per_minute(word_count: int, duration_seconds: float | None) -> float | None:
    """Speaking rate across the whole span, pauses included.

    ``None`` when there is no duration to divide by, or nothing was said.
    """
    if duration_seconds is None or duration_seconds <= 0 or word_count <= 0:
        return None
    return _round_rate(word_count * _SECONDS_PER_MINUTE / duration_seconds)


def articulation_rate_wpm(
    word_count: int,
    span_seconds: float | None,
    pause_seconds: float,
) -> float | None:
    """Speaking rate with pause time removed.

    ``None`` when there are no timings, nothing was said, or the pauses account
    for the entire span — a rate over zero phonation time is not a number.
    """
    if span_seconds is None or word_count <= 0:
        return None
    phonation = span_seconds - pause_seconds
    if phonation <= 0:
        return None
    return _round_rate(word_count * _SECONDS_PER_MINUTE / phonation)


def count_filler_words(transcript: Transcript) -> FillerWordStats | None:
    """Filler statistics, or ``None`` when this transcript cannot support them.

    A provider that cleans disfluencies out of its output has already removed
    the evidence, so counting against it would report "no fillers" for a
    recording full of them. Those transcripts get ``None``.

    Phrases are matched longest-first over the token stream and consume the
    tokens they match, so "you know" is counted once as a phrase rather than
    also contributing a bare "know".
    """
    if not transcript.includes_disfluencies:
        return None

    tokens = word_tokens(transcript)
    counts: Counter[tuple[str, FillerCategory]] = Counter()

    index = 0
    while index < len(tokens):
        matched = 0
        for size in range(min(_MAX_PHRASE_TOKENS, len(tokens) - index), 0, -1):
            phrase = " ".join(tokens[index : index + size])
            if size == 1 and phrase in _HESITATIONS:
                counts[(_HESITATIONS[phrase], FillerCategory.HESITATION)] += 1
                matched = size
                break
            if phrase in _DISCOURSE_MARKERS:
                counts[(_DISCOURSE_MARKERS[phrase], FillerCategory.DISCOURSE_MARKER)] += 1
                matched = size
                break
        index += matched or 1

    by_term = tuple(
        FillerTermCount(term=term, category=category, count=count)
        # Sorted for a stable, reproducible ordering: most frequent first, then
        # alphabetically, so equal counts do not depend on dictionary order.
        for (term, category), count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][0]))
    )
    return FillerWordStats(
        hesitation_count=sum(
            item.count for item in by_term if item.category is FillerCategory.HESITATION
        ),
        discourse_marker_count=sum(
            item.count for item in by_term if item.category is FillerCategory.DISCOURSE_MARKER
        ),
        by_term=by_term,
    )


def compute_metrics(
    transcript: Transcript,
    *,
    pause_threshold_seconds: float = DEFAULT_PAUSE_THRESHOLD_SECONDS,
    recording_duration_seconds: float | None = None,
) -> SpeechMetrics:
    """Compute every metric this transcript supports, and omit the rest.

    ``recording_duration_seconds`` lets the caller supply the duration measured
    from the file at upload time. It is used only as a fallback for the rate
    metrics when the provider gave no word timings, and the result records
    which of the two was used, because they measure different things.
    """
    word_count = count_words(transcript)

    span = speaking_span_seconds(transcript)
    if span is not None:
        duration: float | None = span
        duration_source: DurationSource | None = DurationSource.WORD_TIMINGS
    else:
        fallback = recording_duration_seconds
        if fallback is None:
            fallback = transcript.audio_duration_seconds
        if fallback is not None and fallback > 0:
            duration = _round_seconds(fallback)
            duration_source = DurationSource.RECORDING_DURATION
        else:
            duration = None
            duration_source = None

    pauses = find_pauses(transcript, threshold_seconds=pause_threshold_seconds)
    if pauses is None:
        pause_total: float | None = None
        pause_count: int | None = None
        longest: float | None = None
        mean: float | None = None
        threshold: float | None = None
        articulation: float | None = None
    else:
        durations = [pause.duration_seconds for pause in pauses]
        pause_total = total_pause_seconds(pauses)
        pause_count = len(pauses)
        # Count is a real zero when no gap was long enough; the maximum and the
        # mean of an empty set are undefined, so they stay omitted.
        longest = _round_seconds(max(durations)) if durations else None
        mean = _round_seconds(sum(durations) / len(durations)) if durations else None
        threshold = pause_threshold_seconds
        articulation = articulation_rate_wpm(word_count, span, pause_total)

    return SpeechMetrics(
        word_count=word_count,
        duration_source=duration_source if word_count > 0 else None,
        speaking_duration_seconds=duration if word_count > 0 else None,
        words_per_minute=words_per_minute(word_count, duration),
        articulation_rate_wpm=articulation,
        pause_threshold_seconds=threshold,
        pause_count=pause_count,
        total_pause_seconds=pause_total,
        longest_pause_seconds=longest,
        mean_pause_seconds=mean,
        filler_words=count_filler_words(transcript),
    )

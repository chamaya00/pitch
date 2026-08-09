"""Tests for the deterministic speaking metrics.

The arithmetic is easy; the edge cases are the point. Almost every test below
asks the same question in a different way: *when the transcript cannot support
this metric, is it absent rather than zero?*

Fixtures are built by hand with exact timings so every expected number can be
worked out on paper rather than recorded from a run.
"""

import pytest

from app.services.analysis.metrics import (
    DEFAULT_PAUSE_THRESHOLD_SECONDS,
    articulation_rate_wpm,
    compute_metrics,
    count_filler_words,
    count_words,
    find_pauses,
    speaking_span_seconds,
    tokenize,
    total_pause_seconds,
    word_tokens,
    words_per_minute,
)
from app.services.analysis.models import (
    DurationSource,
    FillerCategory,
    Provenance,
    Transcript,
    TranscriptWord,
)

PROVENANCE = Provenance(provider="mock", is_mock=True)


def timed(text: str, start: float, end: float) -> TranscriptWord:
    return TranscriptWord(text=text, start_seconds=start, end_seconds=end)


def transcript(
    *,
    text: str | None = None,
    words: tuple[TranscriptWord, ...] = (),
    verbatim: bool = False,
    audio_duration_seconds: float | None = None,
) -> Transcript:
    return Transcript(
        text=text if text is not None else " ".join(word.text for word in words),
        words=words,
        includes_disfluencies=verbatim,
        audio_duration_seconds=audio_duration_seconds,
        provenance=PROVENANCE,
    )


#: Four words a second apart: 0.0-0.5, 1.0-1.5, 2.0-2.5, 3.0-3.5.
#: Span 3.5 s, three gaps of 0.5 s each — exactly on the pause threshold.
EVENLY_SPACED = tuple(
    timed(word, i * 1.0, i * 1.0 + 0.5) for i, word in enumerate(["one", "two", "three", "four"])
)


# --- Tokenising ------------------------------------------------------------


def test_punctuation_is_not_part_of_a_word():
    assert tokenize("Hello, world! It's fine.") == ("hello", "world", "it's", "fine")


def test_curly_apostrophes_are_normalised():
    assert tokenize("don’t") == ("don't",)


def test_tokens_come_from_provider_words_when_present():
    """The provider's own segmentation wins over re-splitting its prose."""
    words = (timed("Hello,", 0, 0.3), timed("world.", 0.4, 0.8))
    source = transcript(text="ignored entirely", words=words)
    assert word_tokens(source) == ("hello", "world")


def test_tokens_fall_back_to_the_full_text():
    assert word_tokens(transcript(text="Hello world")) == ("hello", "world")


def test_a_word_count_is_always_available():
    """Even a text-only transcript supports this one metric."""
    assert count_words(transcript(text="one two three")) == 3
    assert count_words(transcript(text="")) == 0


def test_punctuation_only_tokens_are_not_counted_as_words():
    assert count_words(transcript(words=(timed("hello", 0, 0.3), timed("--", 0.4, 0.5)))) == 1


# --- Speaking span ---------------------------------------------------------


def test_the_span_runs_from_the_first_word_to_the_last():
    assert speaking_span_seconds(transcript(words=EVENLY_SPACED)) == pytest.approx(3.5)


def test_there_is_no_span_without_timings():
    assert speaking_span_seconds(transcript(text="one two three")) is None


def test_there_is_no_span_when_only_some_words_are_timed():
    """Partial timings are unusable, not partially usable."""
    mixed = (timed("one", 0.0, 0.5), TranscriptWord(text="two"))
    assert speaking_span_seconds(transcript(words=mixed)) is None


def test_a_zero_length_span_is_reported_as_no_span():
    single = (timed("one", 1.0, 1.0),)
    assert speaking_span_seconds(transcript(words=single)) is None


# --- Pause detection -------------------------------------------------------


def test_pauses_are_none_without_timings():
    """Nothing to detect is not the same as detecting nothing."""
    assert find_pauses(transcript(text="one two three")) is None


def test_no_long_enough_gap_yields_an_empty_tuple():
    close = (timed("one", 0.0, 0.5), timed("two", 0.6, 1.0))
    assert find_pauses(transcript(words=close)) == ()


def test_a_gap_exactly_at_the_threshold_counts():
    pauses = find_pauses(transcript(words=EVENLY_SPACED))
    assert pauses is not None
    assert len(pauses) == 3
    assert all(pause.duration_seconds == pytest.approx(0.5) for pause in pauses)


def test_a_gap_just_below_the_threshold_does_not_count():
    words = (timed("one", 0.0, 0.5), timed("two", 0.999, 1.4))
    assert find_pauses(transcript(words=words)) == ()


def test_the_threshold_is_adjustable():
    source = transcript(words=EVENLY_SPACED)
    assert find_pauses(source, threshold_seconds=0.4) is not None
    assert len(find_pauses(source, threshold_seconds=0.6) or ()) == 0


def test_a_non_positive_threshold_is_rejected():
    with pytest.raises(ValueError, match="must be positive"):
        find_pauses(transcript(words=EVENLY_SPACED), threshold_seconds=0)


def test_words_are_read_in_timing_order_not_list_order():
    shuffled = (EVENLY_SPACED[2], EVENLY_SPACED[0], EVENLY_SPACED[3], EVENLY_SPACED[1])
    assert find_pauses(transcript(words=shuffled)) == find_pauses(transcript(words=EVENLY_SPACED))


def test_overlapping_words_produce_no_pause():
    """Some providers emit overlapping segments; a negative gap is not a pause."""
    overlapping = (timed("one", 0.0, 2.0), timed("two", 1.0, 3.0))
    assert find_pauses(transcript(words=overlapping)) == ()


def test_a_word_nested_inside_another_does_not_hide_a_later_pause():
    nested = (timed("one", 0.0, 5.0), timed("two", 1.0, 2.0), timed("three", 6.0, 6.5))
    pauses = find_pauses(transcript(words=nested))
    assert pauses is not None
    assert len(pauses) == 1
    assert pauses[0].duration_seconds == pytest.approx(1.0)


def test_total_pause_time_adds_up():
    pauses = find_pauses(transcript(words=EVENLY_SPACED))
    assert pauses is not None
    assert total_pause_seconds(pauses) == pytest.approx(1.5)
    assert total_pause_seconds(()) == 0.0


# --- Rates -----------------------------------------------------------------


def test_words_per_minute_is_words_over_the_span():
    assert words_per_minute(30, 60.0) == 30.0
    assert words_per_minute(4, 3.5) == pytest.approx(68.6, abs=0.05)


@pytest.mark.parametrize(
    ("count", "duration"),
    [(0, 60.0), (10, None), (10, 0.0), (10, -1.0)],
)
def test_a_rate_without_a_denominator_or_a_numerator_is_omitted(count: int, duration: float | None):
    assert words_per_minute(count, duration) is None


def test_articulation_rate_removes_pause_time():
    # 4 words, 3.5 s span, 1.5 s of pauses -> 2.0 s of phonation -> 120 wpm.
    assert articulation_rate_wpm(4, 3.5, 1.5) == pytest.approx(120.0)


def test_articulation_rate_is_omitted_without_a_span():
    assert articulation_rate_wpm(4, None, 0.0) is None


def test_articulation_rate_is_omitted_when_pauses_consume_the_span():
    """A rate over zero phonation time is not a number."""
    assert articulation_rate_wpm(4, 2.0, 2.0) is None
    assert articulation_rate_wpm(4, 2.0, 3.0) is None


# --- Filler words ----------------------------------------------------------


def test_fillers_are_not_counted_against_a_cleaned_transcript():
    """The evidence was already removed; reporting zero would be a lie."""
    cleaned = transcript(text="um so I was thinking, you know, about it", verbatim=False)
    assert count_filler_words(cleaned) is None


def test_hesitations_and_discourse_markers_are_counted_separately():
    source = transcript(
        text="Um, I was, uh, thinking, you know, that it was like fine.",
        verbatim=True,
    )
    stats = count_filler_words(source)
    assert stats is not None
    assert stats.hesitation_count == 2
    assert stats.discourse_marker_count == 2
    assert stats.total_count == 4


def test_spelling_variants_collapse_onto_one_term():
    stats = count_filler_words(transcript(text="Um umm ummm uh uhh", verbatim=True))
    assert stats is not None
    terms = {item.term: item.count for item in stats.by_term}
    assert terms == {"um": 3, "uh": 2}


def test_a_phrase_is_counted_once_and_consumes_its_words():
    """'you know' must not also register a bare 'know'."""
    stats = count_filler_words(transcript(text="you know you know", verbatim=True))
    assert stats is not None
    assert stats.discourse_marker_count == 2
    assert [item.term for item in stats.by_term] == ["you know"]


def test_a_verbatim_transcript_with_no_fillers_reports_zero():
    """Zero here is measured, not a stand-in for unknown."""
    stats = count_filler_words(transcript(text="The report is ready.", verbatim=True))
    assert stats is not None
    assert stats.total_count == 0
    assert stats.by_term == ()


def test_categories_are_recorded_per_term():
    stats = count_filler_words(transcript(text="um like", verbatim=True))
    assert stats is not None
    categories = {item.term: item.category for item in stats.by_term}
    assert categories == {
        "um": FillerCategory.HESITATION,
        "like": FillerCategory.DISCOURSE_MARKER,
    }


def test_term_ordering_is_stable():
    source = transcript(text="like like um you know you know you know", verbatim=True)
    first = count_filler_words(source)
    second = count_filler_words(source)
    assert first == second
    assert first is not None
    assert [item.term for item in first.by_term] == ["you know", "like", "um"]


# --- compute_metrics -------------------------------------------------------


def test_a_fully_timed_transcript_yields_every_metric():
    metrics = compute_metrics(transcript(words=EVENLY_SPACED, verbatim=True))

    assert metrics.word_count == 4
    assert metrics.duration_source is DurationSource.WORD_TIMINGS
    assert metrics.speaking_duration_seconds == pytest.approx(3.5)
    assert metrics.words_per_minute == pytest.approx(68.6, abs=0.05)
    assert metrics.articulation_rate_wpm == pytest.approx(120.0)
    assert metrics.pause_count == 3
    assert metrics.total_pause_seconds == pytest.approx(1.5)
    assert metrics.longest_pause_seconds == pytest.approx(0.5)
    assert metrics.mean_pause_seconds == pytest.approx(0.5)
    assert metrics.pause_threshold_seconds == DEFAULT_PAUSE_THRESHOLD_SECONDS
    assert metrics.filler_words is not None


def test_a_text_only_transcript_omits_everything_timing_derived():
    metrics = compute_metrics(transcript(text="one two three four five"))

    assert metrics.word_count == 5
    assert metrics.duration_source is None
    assert metrics.speaking_duration_seconds is None
    assert metrics.words_per_minute is None
    assert metrics.articulation_rate_wpm is None
    assert metrics.pause_count is None
    assert metrics.total_pause_seconds is None
    assert metrics.longest_pause_seconds is None
    assert metrics.mean_pause_seconds is None
    assert metrics.pause_threshold_seconds is None
    assert metrics.filler_words is None


def test_a_recording_duration_rescues_the_speaking_rate_but_not_the_pauses():
    """Knowing how long the file is says nothing about where the silences were."""
    metrics = compute_metrics(transcript(text="one two three"), recording_duration_seconds=30.0)

    assert metrics.duration_source is DurationSource.RECORDING_DURATION
    assert metrics.speaking_duration_seconds == pytest.approx(30.0)
    assert metrics.words_per_minute == pytest.approx(6.0)
    assert metrics.articulation_rate_wpm is None
    assert metrics.pause_count is None


def test_the_providers_duration_is_used_when_the_caller_supplies_none():
    metrics = compute_metrics(transcript(text="one two three", audio_duration_seconds=30.0))
    assert metrics.duration_source is DurationSource.RECORDING_DURATION
    assert metrics.words_per_minute == pytest.approx(6.0)


def test_the_callers_duration_wins_over_the_providers():
    """The file's own measured duration is the more trustworthy of the two."""
    source = transcript(text="one two three", audio_duration_seconds=90.0)
    metrics = compute_metrics(source, recording_duration_seconds=30.0)
    assert metrics.words_per_minute == pytest.approx(6.0)


def test_word_timings_win_over_any_supplied_duration():
    metrics = compute_metrics(transcript(words=EVENLY_SPACED), recording_duration_seconds=600.0)
    assert metrics.duration_source is DurationSource.WORD_TIMINGS
    assert metrics.speaking_duration_seconds == pytest.approx(3.5)


def test_no_pause_found_gives_a_real_zero_but_no_average():
    """A count of zero is measured; the mean and maximum of nothing are not."""
    close = (timed("one", 0.0, 0.5), timed("two", 0.6, 1.0))
    metrics = compute_metrics(transcript(words=close))

    assert metrics.pause_count == 0
    assert metrics.total_pause_seconds == 0.0
    assert metrics.longest_pause_seconds is None
    assert metrics.mean_pause_seconds is None


def test_an_empty_transcript_reports_nothing_but_a_zero_word_count():
    metrics = compute_metrics(transcript(text=""), recording_duration_seconds=30.0)

    assert metrics.word_count == 0
    assert metrics.speaking_duration_seconds is None
    assert metrics.duration_source is None
    assert metrics.words_per_minute is None
    assert metrics.articulation_rate_wpm is None


def test_a_zero_or_negative_recording_duration_is_not_used():
    metrics = compute_metrics(transcript(text="one two"), recording_duration_seconds=0.0)
    assert metrics.duration_source is None
    assert metrics.words_per_minute is None


def test_the_pause_threshold_used_is_recorded_alongside_the_pauses():
    """A pause count means nothing without the threshold that produced it."""
    metrics = compute_metrics(transcript(words=EVENLY_SPACED), pause_threshold_seconds=0.6)
    assert metrics.pause_threshold_seconds == 0.6
    assert metrics.pause_count == 0


def test_metrics_are_reproducible():
    source = transcript(words=EVENLY_SPACED, verbatim=True)
    assert compute_metrics(source) == compute_metrics(source)

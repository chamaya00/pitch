"""How a measurement is expressed, and what a change in it means.

Two enums, shared by everything that puts audio measurements next to each other:
the recording comparison from Step 7N and the progress series from Step 7O. They
live beside the measurements rather than inside either consumer, because they
are facts about the measurements themselves — a cents deviation is in cents and
lower is nearer a note whether you are comparing two takes or plotting twenty.

Duplicating them would be worse than the import: two copies of
``MetricDirection`` is two places for "neutral" to quietly stop meaning
"this difference has no better end", which is the single claim this project most
needs to keep true.
"""

from enum import StrEnum


class MetricUnit(StrEnum):
    """What a value or a difference is expressed in.

    ``PERCENTAGE_POINTS`` exists as its own unit because the alternative is the
    most common numerical lie in software: a ratio moving from 0.50 to 0.56 is
    **6 percentage points**, not 6 percent (it is 12 percent). Ratios are
    converted once, at the edge of the domain, and every consumer reads the unit
    rather than guessing.
    """

    PERCENTAGE_POINTS = "percentage_points"
    CENTS = "cents"
    SEMITONES = "semitones"
    SECONDS = "seconds"


class MetricDirection(StrEnum):
    """Whether a direction of change means anything, and exactly what.

    Only three measurements in this system have a defensible desirable
    direction, and all three are defined relative to **equal temperament**
    rather than to singing: being nearer the nearest semitone, and being
    steadier about it. Everything else is ``NEUTRAL`` — including the detected
    range, which is bounded by what was performed and by the microphone, so a
    wider one is not an achievement.

    Nothing here is skill. Slides, vibrato, blues intonation and any
    non-equal-tempered system move the directed metrics without anything being
    wrong.
    """

    HIGHER_IS_NEARER_THE_NOTE = "higher_is_nearer_the_note"
    LOWER_IS_NEARER_THE_NOTE = "lower_is_nearer_the_note"
    LOWER_IS_STEADIER = "lower_is_steadier"
    NEUTRAL = "neutral"


__all__ = ["MetricDirection", "MetricUnit"]

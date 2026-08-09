# AI interpretation layer

> **Status: design notes only.** Implemented in Phase 6.

## Role

The LLM explains measurements. It does not produce them.

| Deterministic audio engine | LLM |
| --- | --- |
| Frequencies, notes, cents | Plain-language explanation |
| Vocal range, semitone span | Pattern description |
| Stability statistics | Personalised feedback |
| Loudness, spectral features | Practice exercises and plans |
| Comparisons between recordings (the numbers) | Comparisons (the narrative) |

If a number appears in the UI, it came from `services/analysis`. The prompt
supplies the LLM with an already-computed payload, and the response is validated
before display.

## Input payload

```json
{
  "range": { "lowest": "G2", "highest": "C5" },
  "pitch_accuracy": 82.4,
  "average_deviation": -17.2,
  "unstable_notes": [{ "note": "A4", "average_deviation": -34 }],
  "spectral": { "centroid": 2180 }
}
```

The payload carries measurements only — no filenames, no user identifiers, no
raw audio.

## Prompt

```
You are a vocal practice coach.

Analyze the supplied objective audio measurements.

Do not invent measurements.
Do not diagnose medical conditions.
Do not make claims about vocal health.

Explain the results in beginner-friendly language.

Structure your response as:

1. Overall summary
2. Strong points
3. Areas to improve
4. Pitch observations
5. Range observations
6. Suggested practice exercises
7. One short practice plan

If the data is insufficient to support a conclusion, explicitly say so.
```

## Output

Structured JSON is the canonical stored representation. Markdown blobs are not
stored as canonical data — the frontend renders the structure.

```json
{
  "summary": "…",
  "strengths": [],
  "areas_to_improve": [],
  "pitch_observations": [],
  "range_observations": [],
  "exercises": [],
  "practice_plan": []
}
```

The response is parsed and validated against a Pydantic schema. A malformed
response is a failure (`AI_UNAVAILABLE`), not something to paper over with raw
text.

## Failure handling

The AI layer is an enhancement, never a prerequisite. If `ANTHROPIC_API_KEY` is
missing or the provider is unreachable, analysis results still render in full;
only the feedback panel shows an error state.

## Safety

The prompt forbids medical claims, and the UI carries the standing disclaimer:

> This analysis is only an audio-based estimate and is not a medical or
> professional vocal assessment.

Never sent to the provider: API keys of other services, raw audio, user email or
name.

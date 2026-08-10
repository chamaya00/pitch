"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { formatDuration } from "@/lib/format";
import type { Transcript } from "@/types/api";

interface AnalysisTranscriptProps {
  transcript: Transcript;
}

const COLLAPSED_CHARS = 600;

/**
 * What the provider heard.
 *
 * The prose is the transcript's own text, so nothing is reconstructed from the
 * word list. Timings are shown only where the provider actually supplied
 * them — an untimed word gets no timestamp rather than an invented one.
 *
 * Long transcripts collapse by default so the result stays scannable, and
 * `break-words` keeps an unbroken run of characters from widening the layout.
 */
export function AnalysisTranscript({ transcript }: AnalysisTranscriptProps) {
  const [expanded, setExpanded] = useState(false);
  const long = transcript.text.length > COLLAPSED_CHARS;
  const shown =
    long && !expanded
      ? `${transcript.text.slice(0, COLLAPSED_CHARS).trimEnd()}…`
      : transcript.text;

  const timedWords = transcript.words.filter(
    (word) => word.start_seconds !== null,
  );

  return (
    <section
      aria-labelledby="transcript-heading"
      className="rounded-xl border border-border bg-surface"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border p-5">
        <h3 id="transcript-heading" className="text-sm font-medium">
          Transcript
        </h3>
        <p className="text-xs text-muted">
          {transcript.language && (
            <span className="uppercase">{transcript.language}</span>
          )}
          {transcript.language && transcript.audio_duration_seconds !== null && (
            <span aria-hidden> · </span>
          )}
          {transcript.audio_duration_seconds !== null &&
            formatDuration(transcript.audio_duration_seconds)}
        </p>
      </header>

      <div className="p-5">
        <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">
          {shown}
        </p>

        {long && (
          <Button
            variant="secondary"
            className="mt-4"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? "Show less" : "Show full transcript"}
          </Button>
        )}

        {timedWords.length > 0 && (
          <p className="mt-4 text-xs text-muted">
            {timedWords.length} of {transcript.words.length} words carry
            timings, which is what the pause and articulation measurements are
            built from.
          </p>
        )}
      </div>
    </section>
  );
}

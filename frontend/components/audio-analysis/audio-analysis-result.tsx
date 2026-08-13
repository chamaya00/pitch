"use client";

import { AudioFeedbackCard } from "@/components/audio-analysis/audio-feedback-card";
import { NoteBreakdown } from "@/components/audio-analysis/note-breakdown";
import { PitchGraph } from "@/components/audio-analysis/pitch-graph";
import {
  loudnessRows,
  rangeLabel,
  rangeRows,
  spectralRows,
  stabilityRows,
  type MetricRow,
} from "@/lib/audio-analysis-metrics";
import type {
  AudioSummary,
  NoteBreakdown as NoteBreakdownData,
  PitchPoint,
} from "@/types/api";

interface AudioAnalysisResultProps {
  summary: AudioSummary;
  timeline: PitchPoint[] | null;
  timelineError: string | null;
  breakdown: NoteBreakdownData | null;
  breakdownError: string | null;
  /** The recording the AI card asks about. Measurements never leave this page. */
  recordingId: string;
}

/**
 * The measured audio, in the order the reader needs it.
 *
 * Headline range first, then the graph it came from, then the numbers behind
 * both. There is deliberately no single figure at the top: a range, a
 * consistency share and a loudness reading answer different questions, and
 * averaging them into one would produce a grade nobody could act on.
 *
 * Every number here was computed from the signal. None of it came from a
 * language model, and none of it is comparable with the live estimate shown
 * while recording — that runs a different algorithm over a different window.
 */
export function AudioAnalysisResult({
  summary,
  timeline,
  timelineError,
  breakdown,
  breakdownError,
  recordingId,
}: AudioAnalysisResultProps) {
  const range = rangeLabel(summary.range);
  const consistency = summary.stability.in_tune_ratio;

  return (
    <div className="fade-in space-y-4">
      <div className="grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2">
        <div className="bg-surface px-5 py-6">
          <p className="text-xs text-muted">Detected range</p>
          <p className="mt-1 font-mono text-3xl tabular-nums">
            {range ?? <span className="text-xl text-muted">Not measured</span>}
          </p>
          <p className="mt-2 text-xs leading-relaxed text-muted">
            What this recording contained — not the limit of your voice.
          </p>
        </div>
        <div className="bg-surface px-5 py-6">
          <p className="text-xs text-muted">Pitch consistency</p>
          <p className="mt-1 font-mono text-3xl tabular-nums">
            {consistency === null ? (
              <span className="text-xl text-muted">Not measured</span>
            ) : (
              `${Math.round(consistency * 100)}%`
            )}
          </p>
          <p className="mt-2 text-xs leading-relaxed text-muted">
            Share of voiced frames within 25 cents of the nearest note. Slides,
            vibrato and bends lower it without anything being wrong.
          </p>
        </div>
      </div>

      <section
        aria-labelledby="pitch-graph-heading"
        className="rounded-xl border border-border bg-surface p-5"
      >
        <h4 id="pitch-graph-heading" className="text-sm font-medium">
          Pitch
        </h4>
        <div className="mt-4">
          {timeline && timeline.length > 0 ? (
            <PitchGraph points={timeline} durationSeconds={summary.duration_seconds} />
          ) : (
            <p className="text-sm text-muted">
              {timelineError ??
                (timeline === null
                  ? "Loading the pitch graph…"
                  : "No pitch points to draw.")}
            </p>
          )}
        </div>
      </section>

      <NoteBreakdown breakdown={breakdown} error={breakdownError} />

      <MetricGroup
        id="audio-range"
        title="Detected range"
        rows={rangeRows(summary.range)}
      />
      <MetricGroup
        id="audio-stability"
        title="Pitch stability"
        rows={stabilityRows(summary.stability)}
        note="These describe how steady the pitch was. They are not a measure of singing ability, and there is no reference melody to compare against."
      />
      <MetricGroup
        id="audio-loudness"
        title="Loudness"
        rows={loudnessRows(summary.loudness)}
        note="Amplitude measurements. These are not LUFS and imply nothing about mastering-grade loudness."
      />
      <MetricGroup
        id="audio-spectral"
        title="Spectral characteristics"
        rows={spectralRows(summary.spectral)}
        note="Raw measurable characteristics of the signal. VocalLens does not turn these into words like “bright” or “breathy” — that needs a validated classifier, and there isn't one here."
      />

      <AudioFeedbackCard recordingId={recordingId} />

      <p className="px-1 text-xs leading-relaxed text-muted">
        An audio-based estimate from one recording, measured at{" "}
        {summary.settings.sample_rate_hz.toLocaleString()} Hz over{" "}
        {summary.settings.frame_length_samples}-sample frames. It is not a
        professional, clinical or educational assessment.
      </p>
    </div>
  );
}

function MetricGroup({
  id,
  title,
  rows,
  note,
}: {
  id: string;
  title: string;
  rows: MetricRow[];
  note?: string;
}) {
  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="rounded-xl border border-border bg-surface"
    >
      <h4 id={`${id}-heading`} className="border-b border-border px-5 py-4 text-sm font-medium">
        {title}
      </h4>
      <dl className="grid gap-px overflow-hidden bg-border sm:grid-cols-2">
        {rows.map((row) => (
          <div key={row.label} className="bg-surface px-5 py-4">
            <dt className="text-xs text-muted">{row.label}</dt>
            <dd
              className={`mt-1 font-mono text-sm tabular-nums ${
                row.measured ? "" : "text-muted"
              }`}
            >
              {row.value}
            </dd>
            {row.hint && (
              <p className="mt-1 text-xs leading-relaxed text-muted">{row.hint}</p>
            )}
          </div>
        ))}
        {/* An odd row count leaves a hole in the two-column grid, and the hole
            shows as a block of the divider colour. */}
        {rows.length % 2 === 1 && <div aria-hidden className="hidden bg-surface sm:block" />}
      </dl>
      {note && (
        <p className="border-t border-border px-5 py-4 text-xs leading-relaxed text-muted">
          {note}
        </p>
      )}
    </section>
  );
}

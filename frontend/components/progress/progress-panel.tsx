"use client";

import { useState } from "react";
import { ProgressChart } from "@/components/progress/progress-chart";
import { Button } from "@/components/ui/button";
import { useProgress } from "@/hooks/use-progress";
import {
  NOT_MEASURED,
  depthMessage,
  formatPointDateTime,
  formatValue,
  observationMessage,
  pointStatusMessage,
} from "@/lib/progress";
import type {
  ProgressHistory,
  ProgressMetricKey,
  ProgressRecording,
  ProgressSeries,
} from "@/types/api";

/**
 * Your recorded measurements over time.
 *
 * That sentence is the entire product claim, and the copy on this panel never
 * exceeds it. There is no score, no level and no statement that the singing
 * improved; four of the seven series carry no desirable direction at all, and
 * the observation under the chart says what changed without saying it was
 * progress.
 *
 * Two recordings are not a trend, and the panel says so rather than letting a
 * line between two dots imply otherwise.
 *
 * The caveat sits above the chart because it changes how the chart should be
 * read: every point came from a recording made under conditions this system
 * cannot measure.
 */
export function ProgressPanel() {
  const { state, reload } = useProgress();
  const [metric, setMetric] = useState<ProgressMetricKey>("in_tune_ratio");

  return (
    <section
      aria-labelledby="progress-heading"
      className="border-t border-border pt-10"
    >
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="progress-heading" className="text-sm font-medium">
            Your measurements over time
          </h2>
          <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
            How the numbers measured from your recordings have changed. These are
            measurements, not a score — and most of them have no better
            direction.
          </p>
        </div>
        {state.status === "ready" && state.history.recordings.length > 0 && (
          <Button variant="secondary" onClick={reload}>
            Refresh
          </Button>
        )}
      </div>

      <p aria-live="polite" className="sr-only">
        {announcement(state)}
      </p>

      {state.status === "loading" && (
        <p className="mt-6 text-sm text-muted">Loading your measurements…</p>
      )}

      {state.status === "error" && (
        <div
          role="alert"
          className="mt-6 rounded-xl border border-danger/40 bg-danger/5 p-5"
        >
          <p className="text-sm font-medium text-danger">
            Couldn&apos;t load your measurements
          </p>
          <p className="mt-1 text-sm text-muted">{state.message}</p>
          <div className="mt-4">
            <Button variant="secondary" onClick={reload}>
              Try again
            </Button>
          </div>
        </div>
      )}

      {state.status === "ready" && (
        <Result history={state.history} metric={metric} onSelect={setMetric} />
      )}
    </section>
  );
}

function Result({
  history,
  metric,
  onSelect,
}: {
  history: ProgressHistory;
  metric: ProgressMetricKey;
  onSelect: (metric: ProgressMetricKey) => void;
}) {
  if (history.depth === "empty") {
    return (
      <p className="mt-6 max-w-prose text-sm leading-relaxed text-muted">
        {depthMessage("empty")} Upload a recording and choose &ldquo;Measure the
        audio&rdquo; to start.
      </p>
    );
  }

  const series = history.series.find((item) => item.metric === metric) ?? history.series[0];
  const note = depthMessage(history.depth);

  return (
    <div className="fade-in mt-6 space-y-5">
      {/* Above the chart, because it changes how the chart should be read. */}
      <div role="note" className="rounded-xl border border-border bg-surface-raised p-4">
        <p className="text-xs font-medium">How to read this</p>
        <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
          Each point is one recording, made under conditions VocalLens cannot
          measure. Microphone, room, distance, how hard you were trying and how
          you felt all move these numbers, and none of them is measured or
          corrected for — so a change between two points may be entirely about
          the room.
        </p>
        {note && (
          <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted">{note}</p>
        )}
      </div>

      <MetricSelector series={history.series} selected={series.metric} onSelect={onSelect} />

      <div className="rounded-xl border border-border bg-surface p-5">
        <h3 className="text-sm font-medium">{series.label}</h3>
        <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
          {directionNote(series)}
        </p>
        <div className="mt-4">
          <ProgressChart series={series} />
        </div>
        <p className="mt-4 max-w-prose text-sm leading-relaxed">
          {observationMessage(series)}
        </p>
      </div>

      <ProgressTable history={history} series={series} />
    </div>
  );
}

function MetricSelector({
  series,
  selected,
  onSelect,
}: {
  series: ProgressSeries[];
  selected: ProgressMetricKey;
  onSelect: (metric: ProgressMetricKey) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Choose a measurement"
      className="flex flex-wrap gap-2"
    >
      {series.map((item) => (
        <button
          key={item.metric}
          type="button"
          aria-pressed={item.metric === selected}
          onClick={() => onSelect(item.metric)}
          className={`rounded-md border px-3 py-2 text-xs font-medium transition-colors ${
            item.metric === selected
              ? "border-accent bg-accent text-accent-foreground"
              : "border-border text-muted hover:text-foreground"
          }`}
        >
          {SHORT_LABELS[item.metric]}
        </button>
      ))}
    </div>
  );
}

/** Short names for the selector. The full label sits above the chart. */
const SHORT_LABELS: Record<ProgressMetricKey, string> = {
  in_tune_ratio: "In-tune share",
  mean_abs_cents_deviation: "Distance from note",
  cents_std: "Pitch spread",
  semitone_span: "Detected range",
  voiced_ratio: "Voiced share",
  voiced_seconds: "Pitched time",
  duration_seconds: "Recording length",
};

/** What a change in this series means, if anything. */
function directionNote(series: ProgressSeries): string {
  switch (series.direction) {
    case "higher_is_nearer_the_note":
      return "Higher means more of the pitched time sat within 25 cents of a note. Slides, vibrato and bends lower it without anything being wrong.";
    case "lower_is_nearer_the_note":
      return "Lower means the pitch sat nearer the nearest note on average. It is a distance in cents, not a measure of singing.";
    case "lower_is_steadier":
      return "Lower means the pitch sat more consistently relative to the note. Vibrato and intentional movement raise it.";
    case "neutral":
      return "This has no better direction. A change here describes what was recorded — not an improvement.";
  }
}

function ProgressTable({
  history,
  series,
}: {
  history: ProgressHistory;
  series: ProgressSeries;
}) {
  const byId = new Map<string, ProgressRecording>(
    history.recordings.map((recording) => [recording.recording_id, recording]),
  );

  return (
    <section aria-labelledby="progress-table-heading">
      <h3 id="progress-table-heading" className="text-sm font-medium">
        Every recording
      </h3>
      {/* The table scrolls inside its own container so the page never does. */}
      <div className="mt-3 overflow-x-auto rounded-xl border border-border">
        <table className="w-full min-w-[34rem] border-collapse text-sm">
          <caption className="sr-only">
            Every recording in this window, oldest first, with its value for{" "}
            {series.label}. Recordings that could not be measured are listed with
            the reason rather than a zero.
          </caption>
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted">
              <th scope="col" className="px-4 py-3 font-medium">
                Recording
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                When
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                {series.label}
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                Detected range
              </th>
            </tr>
          </thead>
          <tbody>
            {series.points.map((point) => {
              const recording = byId.get(point.recording_id);
              const absence = pointStatusMessage(point);
              return (
                <tr
                  key={point.recording_id}
                  className="border-b border-border align-top last:border-0"
                >
                  <th scope="row" className="px-4 py-3 text-left font-normal">
                    {recording?.original_filename ?? "Recording"}
                  </th>
                  <td className="px-4 py-3 text-muted">
                    {formatPointDateTime(point.recorded_at)}
                  </td>
                  <td
                    className={`px-4 py-3 font-mono tabular-nums ${
                      point.value === null ? "text-muted" : ""
                    }`}
                  >
                    {formatValue(point.value, series.unit)}
                    {absence && (
                      <span className="mt-1 block font-sans text-xs leading-relaxed text-muted">
                        {absence}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono tabular-nums text-muted">
                    {recording?.lowest_note && recording?.highest_note
                      ? `${recording.lowest_note}–${recording.highest_note}`
                      : NOT_MEASURED}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-3 max-w-prose text-xs leading-relaxed text-muted">
        The detected range is what each recording contained — never the limit of
        your voice. Nothing on this page is a score, a level or an assessment of
        your singing.
      </p>
    </section>
  );
}

function announcement(state: ReturnType<typeof useProgress>["state"]): string {
  switch (state.status) {
    case "loading":
      return "Loading your measurements over time.";
    case "error":
      return `Your measurements could not be loaded. ${state.message}`;
    case "ready":
      return state.history.depth === "empty"
        ? "You have no measured recordings yet."
        : `${state.history.recordings.length} recordings in your measurement history.`;
  }
}

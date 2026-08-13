"use client";

import { MockDataBanner } from "@/components/analysis/mock-data-banner";
import { Button } from "@/components/ui/button";
import { useAudioFeedback } from "@/hooks/use-audio-feedback";
import type { AudioFeedback } from "@/types/api";

interface AudioFeedbackCardProps {
  recordingId: string;
}

/** The sections, in the order a reader wants them. */
const SECTIONS: { key: keyof AudioFeedback; title: string }[] = [
  { key: "strengths", title: "What looks good" },
  { key: "areas_to_improve", title: "What to work on" },
  { key: "pitch_observations", title: "Pitch observations" },
  { key: "range_observations", title: "Range observations" },
  { key: "note_observations", title: "Note observations" },
  { key: "audio_observations", title: "Audio observations" },
  { key: "exercises", title: "Practice exercises" },
  { key: "practice_plan", title: "Practice plan" },
];

/**
 * A language model's reading of the measurements above.
 *
 * Deliberately the last card, deliberately its own card, and deliberately
 * labelled as generated. Everything above it was computed by the analysis
 * pipeline; this is prose *about* those numbers, and a reader must never come
 * away thinking a model measured anything.
 *
 * Nothing is requested until the button is pressed. A model is consulted
 * because someone asked, not because a page rendered — and the runner behind
 * this makes a second click a no-op rather than a second paid call.
 *
 * A failure here costs the prose and nothing else. The measurements stay
 * exactly where they are, and the copy says so.
 */
export function AudioFeedbackCard({ recordingId }: AudioFeedbackCardProps) {
  const { state, start } = useAudioFeedback(recordingId);
  const feedback = state.status === "completed" ? state.analysis.feedback : null;

  return (
    <section
      aria-labelledby="audio-feedback-heading"
      className="rounded-xl border border-border bg-surface"
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border p-5">
        <div>
          <h4 id="audio-feedback-heading" className="text-sm font-medium">
            AI vocal feedback
          </h4>
          <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
            An AI interpretation of the measured audio above. It explains those
            measurements; it does not produce them, and it never sees the
            recording.
          </p>
        </div>
        {state.status === "idle" && (
          <Button variant="secondary" onClick={start}>
            Explain these results
          </Button>
        )}
      </header>

      <p aria-live="polite" className="sr-only">
        {announcement(state.status)}
      </p>

      {(state.status === "starting" || state.status === "running") && (
        <p className="p-5 text-sm text-muted">Preparing your feedback…</p>
      )}

      {(state.status === "failed" || state.status === "error") && (
        <div className="p-5">
          <p className="text-sm font-medium">
            We couldn&apos;t generate feedback right now.
          </p>
          <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted">
            {state.message} Your measured audio results above are unaffected —
            they were computed without a model.
          </p>
          <div className="mt-4">
            <Button variant="secondary" onClick={start}>
              Try again
            </Button>
          </div>
        </div>
      )}

      {feedback && (
        <div className="fade-in space-y-5 p-5">
          {feedback.provenance.is_mock && (
            <MockDataBanner>
              No feedback provider is configured, so this interpretation came
              from a development stand-in rather than from a language model. The
              measurements above are real; the words below are not an
              interpretation of them.
            </MockDataBanner>
          )}

          <p className="max-w-prose text-sm leading-relaxed">{feedback.summary}</p>

          {SECTIONS.map(({ key, title }) => {
            const items = feedback[key];
            // An empty section is omitted rather than rendered as a heading
            // with "None" under it — a model having nothing to say about
            // range is not a finding about the range.
            if (!Array.isArray(items) || items.length === 0) return null;
            return (
              <div key={key}>
                <h5 className="text-xs font-medium uppercase tracking-wide text-muted">
                  {title}
                </h5>
                <ul className="mt-2 space-y-1.5">
                  {items.map((item) => (
                    <li
                      key={item}
                      className="max-w-prose text-sm leading-relaxed before:mr-2 before:text-muted before:content-['—']"
                    >
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}

          <p className="border-t border-border pt-4 text-xs leading-relaxed text-muted">
            Written by {feedback.provenance.is_mock ? "a development stand-in" : "a language model"}{" "}
            from the measurements above. It is not a professional, clinical or
            educational assessment, and it cannot tell you the limits of your
            voice from one recording.
          </p>
        </div>
      )}
    </section>
  );
}

function announcement(status: string): string {
  switch (status) {
    case "starting":
    case "running":
      return "Preparing your feedback.";
    case "completed":
      return "Feedback is ready.";
    case "failed":
    case "error":
      return "Feedback could not be generated. Your measured results are unaffected.";
    default:
      return "Feedback has not been requested.";
  }
}

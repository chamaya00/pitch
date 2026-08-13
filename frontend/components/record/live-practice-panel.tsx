"use client";

import { useEffect, useRef, useState } from "react";
import { LivePitchMeter } from "@/components/record/live-pitch-meter";
import { LiveStatsCard } from "@/components/record/live-stats-card";
import { PitchTrace } from "@/components/record/pitch-trace";
import { TargetNotePicker } from "@/components/record/target-note-picker";
import { useLiveStats } from "@/hooks/use-live-stats";
import { compareToTarget, targetLabel } from "@/lib/live-practice";
import { formatCents, formatFrequency, midiToNoteName } from "@/lib/pitch";
import type { LivePitchSample } from "@/lib/pitch-stream";

interface LivePracticePanelProps {
  subscribe: (listener: (sample: LivePitchSample) => void) => () => void;
  /** True only while audio is actually flowing — paused freezes the readout. */
  active: boolean;
  /** Changes when a new take begins, so statistics start fresh. */
  sessionId: number;
}

/**
 * Live Vocal Practice: what you are singing, right now.
 *
 * **This adds no detection.** Every number comes from the sample stream that
 * `lib/live-pitch-engine.ts` already produces — one microphone capture, one
 * AudioWorklet, one NSDF detector, one smoother. This component reads that
 * stream and arranges it.
 *
 * The render budget is the design constraint. Note, cents, frequency, needle
 * and trace are written to the DOM from the subscription and never through
 * React; only the session summary reaches state, twice a second. The whole card
 * therefore renders on a target change and a stats tick, not thirty times a
 * second.
 *
 * Nothing is uploaded from here. The live figures are an immediate estimate and
 * say so; the authoritative measurement is the audio analysis of the saved
 * recording, which is a different algorithm over a different window and runs
 * only when someone asks for it.
 */
export function LivePracticePanel({
  subscribe,
  active,
  sessionId,
}: LivePracticePanelProps) {
  const [targetMidi, setTargetMidi] = useState<number | null>(null);
  const stats = useLiveStats(subscribe, active, sessionId);

  const noteRef = useRef<HTMLParagraphElement>(null);
  const centsRef = useRef<HTMLParagraphElement>(null);
  const frequencyRef = useRef<HTMLParagraphElement>(null);
  const targetReadingRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (!active) return;

    return subscribe((sample) => {
      // "Listening…" rather than a dash or a zero: the microphone is working,
      // there is simply no pitch in this moment worth naming.
      if (noteRef.current) noteRef.current.textContent = sample.note ?? "Listening…";
      if (centsRef.current) {
        centsRef.current.textContent = sample.voiced
          ? formatCents(sample.cents)
          : "";
      }
      if (frequencyRef.current) {
        frequencyRef.current.textContent = sample.voiced
          ? formatFrequency(sample.frequency)
          : "";
      }
      if (targetReadingRef.current) {
        const comparison = targetMidi === null ? null : compareToTarget(sample, targetMidi);
        targetReadingRef.current.textContent = comparison
          ? `${comparison.detectedNote} · ${targetLabel(comparison) ?? ""}`
          : "";
      }
    });
  }, [subscribe, active, targetMidi]);

  return (
    <section aria-labelledby="live-practice-heading" className="space-y-4">
      <div className="rounded-xl border border-border bg-surface p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h3 id="live-practice-heading" className="text-sm font-medium">
              Live Vocal Practice
            </h3>
            <p className="mt-1 text-xs text-muted">
              Detected in your browser as you sing. Nothing is sent anywhere.
            </p>
          </div>
          <TargetNotePicker value={targetMidi} onChange={setTargetMidi} />
        </div>

        <div className="mt-6 text-center">
          {targetMidi !== null && (
            <p className="font-mono text-xs text-muted">
              Target {midiToNoteName(targetMidi)}
            </p>
          )}

          {/* Fast-changing text. Deliberately outside any live region — a
              screen reader announcing this at frame rate would be unusable. */}
          <p
            ref={noteRef}
            className="mt-1 font-mono text-6xl font-semibold tabular-nums sm:text-7xl"
          >
            Listening…
          </p>
          <p ref={centsRef} className="mt-3 font-mono text-lg tabular-nums text-muted" />
          <p ref={frequencyRef} className="mt-1 font-mono text-xs text-muted" />
          {targetMidi !== null && (
            <p ref={targetReadingRef} className="mt-2 font-mono text-xs text-muted" />
          )}

          <LivePitchMeter
            subscribe={subscribe}
            active={active}
            targetMidi={targetMidi}
          />
        </div>
      </div>

      <PitchTrace subscribe={subscribe} active={active} />

      <LiveStatsCard stats={stats} />

      <p className="px-1 text-xs leading-relaxed text-muted">
        Live pitch detection is an immediate estimate. The full audio analysis is
        calculated separately from the saved recording, by a different method
        over a longer window — the two will not agree exactly, and neither
        validates the other.
      </p>
    </section>
  );
}


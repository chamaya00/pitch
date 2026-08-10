"use client";

import { useEffect, useRef } from "react";
import {
  METER_RANGE_CENTS,
  meterReading,
  tuningDirection,
  tuningLabel,
} from "@/lib/live-practice";
import type { LivePitchSample } from "@/lib/pitch-stream";

interface LivePitchMeterProps {
  subscribe: (listener: (sample: LivePitchSample) => void) => () => void;
  active: boolean;
  /** Compare against a chosen note instead of the nearest one. */
  targetMidi?: number | null;
}

/**
 * A tuner needle: flat on the left, sharp on the right, the note in the middle.
 *
 * Driven straight from the detector's subscription through refs. It renders
 * once and never again — thirty React renders a second to move one marker would
 * re-reconcile the whole page for a `transform`.
 *
 * Three things are deliberate:
 *
 * **The needle is clamped; the number is not.** Past ±50 cents the next note is
 * nearer, so the marker stops at the end — but the text keeps saying the real
 * deviation, and an arrow marks that the needle is pinned. A needle resting at
 * the end must never be read as "exactly 50 cents sharp".
 *
 * **Flat and sharp are words.** The scale ends are labelled, the deviation is
 * spelled out ("23 cents flat"), and the tint is the last cue rather than the
 * only one.
 *
 * **Silence shows nothing.** No needle, no note, no held-over value from a
 * moment ago. The detector is honest about not knowing and so is this.
 */
export function LivePitchMeter({ subscribe, active, targetMidi }: LivePitchMeterProps) {
  const needleRef = useRef<HTMLDivElement>(null);
  const labelRef = useRef<HTMLParagraphElement>(null);
  const pinnedRef = useRef<HTMLSpanElement>(null);

  // Resubscribing when the target changes is fine: it happens when a person
  // picks a note from a list, not thirty times a second.
  const target = targetMidi ?? null;

  useEffect(() => {
    if (!active) return;

    return subscribe((sample) => {
      // Against a chosen note the deviation is measured from *that* note, so a
      // semitone away reads as 100 cents rather than as "in tune, wrong note".
      const cents =
        target !== null && sample.voiced && sample.midi !== null
          ? (sample.midi - target) * 100
          : sample.cents;

      const reading = sample.voiced ? meterReading(cents) : null;

      if (needleRef.current) {
        needleRef.current.style.transform = `translateX(${(reading?.position ?? 0) * 50}%)`;
        needleRef.current.style.opacity = reading ? "1" : "0";
      }
      if (labelRef.current) {
        labelRef.current.textContent = reading ? (tuningLabel(cents) ?? "") : "No pitch detected";
        labelRef.current.dataset.direction = reading
          ? (tuningDirection(cents) ?? "in-tune")
          : "none";
      }
      if (pinnedRef.current) {
        pinnedRef.current.textContent = reading?.clamped
          ? `Beyond ${METER_RANGE_CENTS} cents`
          : "";
      }
    });
  }, [subscribe, active, target]);

  return (
    <div className="mt-6">
      <div
        role="img"
        aria-label={
          `A tuning meter. The marker sits left of centre when the pitch is flat and ` +
          `right of centre when it is sharp, up to ${METER_RANGE_CENTS} cents either way. ` +
          `The reading is written out below it.`
        }
      >
        <div className="relative mx-auto h-8 w-full max-w-sm">
          {/* The scale itself: a rule, ticks at the ends, a stronger one at zero. */}
          <div className="absolute top-1/2 h-px w-full -translate-y-1/2 bg-border" />
          <div className="absolute left-0 top-1/2 h-3 w-px -translate-y-1/2 bg-border" />
          <div className="absolute right-0 top-1/2 h-3 w-px -translate-y-1/2 bg-border" />
          <div className="absolute left-1/2 top-1/2 h-5 w-px -translate-x-1/2 -translate-y-1/2 bg-muted" />

          {/* Half-width track, so a ±100% transform reaches the two ends. */}
          <div className="absolute left-1/4 top-1/2 flex h-full w-1/2 -translate-y-1/2 items-center justify-center">
            <div
              ref={needleRef}
              className="h-6 w-1 rounded-full bg-accent opacity-0 motion-safe:transition-transform motion-safe:duration-75"
            />
          </div>
        </div>

        <div className="mx-auto flex w-full max-w-sm justify-between font-mono text-[10px] text-muted">
          <span>−{METER_RANGE_CENTS} flat</span>
          <span>0</span>
          <span>+{METER_RANGE_CENTS} sharp</span>
        </div>
      </div>

      {/* The reading in words. Not a live region: it changes ~30 times a second,
          and announcing that would make a screen reader unusable. The stats card
          below carries a readable summary that changes twice a second. */}
      <p
        ref={labelRef}
        data-direction="none"
        className="mt-4 text-sm font-medium data-[direction=flat]:text-warning data-[direction=in-tune]:text-positive data-[direction=none]:text-muted data-[direction=sharp]:text-warning"
      >
        No pitch detected
      </p>
      <span ref={pinnedRef} className="font-mono text-[10px] text-muted" />
    </div>
  );
}

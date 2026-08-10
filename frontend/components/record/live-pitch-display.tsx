"use client";

import { useEffect, useRef } from "react";
import { formatCents, formatFrequency } from "@/lib/pitch";
import type { LivePitchSample } from "@/lib/pitch-stream";

interface LivePitchDisplayProps {
  subscribe: (listener: (sample: LivePitchSample) => void) => () => void;
  /** Paused or stopped: freeze the readout rather than showing a stale note. */
  active: boolean;
}

/**
 * The live readout: note, cents, frequency.
 *
 * Written straight to the DOM through refs. This component renders once and
 * then never again — thirty renders a second to change three text nodes would
 * cost the whole page a re-reconciliation each time, and none of these values
 * affect layout.
 *
 * When nothing is confidently voiced the display shows a dash. It never falls
 * back to the last note, and it never shows a note for silence: an invented
 * pitch is worse than an empty one.
 */
export function LivePitchDisplay({ subscribe, active }: LivePitchDisplayProps) {
  const noteRef = useRef<HTMLParagraphElement>(null);
  const centsRef = useRef<HTMLParagraphElement>(null);
  const frequencyRef = useRef<HTMLParagraphElement>(null);
  const tuningRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!active) return;

    return subscribe((sample) => {
      if (noteRef.current) noteRef.current.textContent = sample.note ?? "—";
      if (centsRef.current) {
        centsRef.current.textContent = sample.voiced
          ? formatCents(sample.cents)
          : "listening";
      }
      if (frequencyRef.current) {
        frequencyRef.current.textContent = sample.voiced
          ? formatFrequency(sample.frequency)
          : "—";
      }
      if (tuningRef.current) {
        // ±50 cents maps across the width; dead centre is in tune.
        const offset =
          sample.voiced && sample.cents !== null
            ? Math.max(-50, Math.min(50, sample.cents))
            : 0;
        tuningRef.current.style.transform = `translateX(${offset}%)`;
        tuningRef.current.style.opacity = sample.voiced ? "1" : "0.25";
      }
    });
  }, [subscribe, active]);

  return (
    <div className="rounded-xl border border-border bg-surface p-6 text-center">
      <p
        ref={noteRef}
        className="font-mono text-6xl font-semibold tabular-nums sm:text-7xl"
      >
        —
      </p>

      <div className="mx-auto mt-5 h-1.5 w-full max-w-xs rounded-full bg-border">
        <div className="flex h-full items-center justify-center">
          {/* The marker is a fixed-width block nudged by transform only, so
              nothing reflows at frame rate. */}
          <div
            ref={tuningRef}
            aria-hidden
            className="h-3 w-1 rounded-full bg-accent opacity-25 motion-safe:transition-transform motion-safe:duration-75"
          />
        </div>
      </div>

      <p ref={centsRef} className="mt-4 font-mono text-sm text-muted">
        listening
      </p>
      <p ref={frequencyRef} className="mt-1 font-mono text-xs text-muted">
        —
      </p>

      <p className="mt-5 text-xs leading-relaxed text-muted">
        Measured in your browser, live. Nothing is sent anywhere while you
        record.
      </p>
    </div>
  );
}

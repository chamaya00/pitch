"use client";

import { useEffect, useRef } from "react";
import type { LivePitchSample } from "@/lib/pitch-stream";

/** Roughly twenty seconds of scrollback at ~30 frames a second. */
const TRACE_POINTS = 600;

/** The drawn pitch range, in MIDI numbers: ~C2 to ~C6, which covers a voice. */
const MIN_MIDI = 36;
const MAX_MIDI = 84;

interface PitchTraceProps {
  subscribe: (listener: (sample: LivePitchSample) => void) => () => void;
  active: boolean;
}

/**
 * A scrolling trace of the last few seconds of pitch.
 *
 * Canvas, driven by an animation frame, kept entirely outside React. Frames
 * are pushed into a fixed-length ring buffer by the subscription and drawn on
 * the next paint, so the incoming rate and the drawing rate stay decoupled.
 *
 * Unvoiced frames leave a gap. Joining across silence would draw a line
 * through pitches that were never sung.
 */
export function PitchTrace({ subscribe, active }: PitchTraceProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pointsRef = useRef<(number | null)[]>([]);

  useEffect(() => {
    const unsubscribe = subscribe((sample) => {
      const points = pointsRef.current;
      points.push(sample.voiced ? sample.midi : null);
      if (points.length > TRACE_POINTS) points.shift();
    });
    return unsubscribe;
  }, [subscribe]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    // Nothing is arriving while paused or stopped; the canvas keeps whatever
    // it last drew rather than burning a frame callback on an unchanged trace.
    if (!active) return;

    let frame = 0;
    let stopped = false;

    const draw = () => {
      if (stopped) return;

      const ratio = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
        canvas.width = width * ratio;
        canvas.height = height * ratio;
      }

      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);

      const style = getComputedStyle(canvas);
      const stroke = style.getPropertyValue("--trace-color").trim() || "#888";

      context.lineWidth = 2;
      context.lineJoin = "round";
      context.lineCap = "round";
      context.strokeStyle = stroke;

      const points = pointsRef.current;
      const step = width / TRACE_POINTS;
      // Right-aligned: the newest frame sits at the right edge and the trace
      // grows leftwards, so a short recording does not stretch to fill.
      const startX = width - points.length * step;

      context.beginPath();
      let drawing = false;
      for (let i = 0; i < points.length; i++) {
        const midi = points[i];
        if (midi === null) {
          drawing = false;
          continue;
        }
        const clamped = Math.max(MIN_MIDI, Math.min(MAX_MIDI, midi));
        const x = startX + i * step;
        const y = height - ((clamped - MIN_MIDI) / (MAX_MIDI - MIN_MIDI)) * height;
        if (drawing) {
          context.lineTo(x, y);
        } else {
          context.moveTo(x, y);
          drawing = true;
        }
      }
      context.stroke();

      frame = requestAnimationFrame(draw);
    };

    frame = requestAnimationFrame(draw);
    return () => {
      stopped = true;
      cancelAnimationFrame(frame);
    };
  }, [active]);

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <canvas
        ref={canvasRef}
        role="img"
        aria-label="A scrolling trace of the pitch detected in the last few seconds."
        className="h-24 w-full [--trace-color:var(--accent)] sm:h-32"
      />
    </div>
  );
}

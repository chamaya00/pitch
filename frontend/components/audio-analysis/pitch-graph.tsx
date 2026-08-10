"use client";

import { useEffect, useRef } from "react";
import type { PitchPoint } from "@/types/api";

interface PitchGraphProps {
  points: PitchPoint[];
  durationSeconds: number;
}

/** Room on the left for the note names drawn on the canvas. */
const AXIS_WIDTH = 30;

/** Padding above and below the sung range, in semitones, so the line breathes. */
const MIDI_PADDING = 3;
/** Fewest semitones the axis will span, so a monotone is not drawn as a cliff. */
const MIN_MIDI_SPAN = 12;

/**
 * The measured pitch over time.
 *
 * Drawn from the backend's own timeline and nothing else. There are no
 * interpolated points, no smoothing applied for looks, and no invented values
 * where the recording was unvoiced: **a gap in the data is drawn as a gap in
 * the line**, because joining across one would show a pitch that was never
 * sung.
 *
 * Canvas rather than SVG: a few hundred points as DOM nodes is a lot of layout
 * for a picture that never needs to be hit-tested. Octave labels are drawn on
 * the canvas rather than laid out beside it, so a label always sits exactly on
 * the line it names; the numbers a screen reader needs are in the summary rows
 * above, and the canvas carries a description of what the line does.
 */
export function PitchGraph({ points, durationSeconds }: PitchGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || points.length === 0) return;

    const draw = () => {
      const context = canvas.getContext("2d");
      if (!context) return;

      const ratio = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      canvas.width = Math.max(1, Math.round(width * ratio));
      canvas.height = Math.max(1, Math.round(height * ratio));
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);

      const style = getComputedStyle(canvas);
      const line = style.getPropertyValue("--graph-line").trim() || "#888";
      const grid = style.getPropertyValue("--graph-grid").trim() || "#ddd";
      const label = style.getPropertyValue("--graph-label").trim() || "#888";

      const [low, high] = midiBounds(points);
      const span = high - low;
      const duration = Math.max(durationSeconds, 0.001);

      const plotWidth = Math.max(width - AXIS_WIDTH, 1);
      const x = (seconds: number) => AXIS_WIDTH + (seconds / duration) * plotWidth;
      const y = (midi: number) => height - ((midi - low) / span) * height;

      // Octave lines, so the vertical axis has a scale rather than a shape.
      // Labels are drawn *on the canvas*, at the same y as the line they name:
      // an axis label a few pixels away from its gridline is a misread waiting
      // to happen, and no amount of flexbox keeps HTML text aligned to a
      // canvas coordinate.
      context.strokeStyle = grid;
      context.fillStyle = label;
      context.font = "10px ui-monospace, monospace";
      context.textBaseline = "middle";
      context.lineWidth = 1;
      for (let midi = Math.ceil(low / 12) * 12; midi <= high; midi += 12) {
        const position = Math.round(y(midi)) + 0.5;
        context.beginPath();
        context.moveTo(AXIS_WIDTH, position);
        context.lineTo(width, position);
        context.stroke();
        // Clamped so a label on the very top or bottom line is still legible;
        // the line itself stays where the pitch actually is.
        context.fillText(noteName(midi), 0, clamp(position, 6, height - 6));
      }

      context.strokeStyle = line;
      context.lineWidth = 2;
      context.lineJoin = "round";
      context.lineCap = "round";
      context.beginPath();

      // A gap wider than a few frames means unvoiced audio. Lift the pen.
      const gapLimit = Math.max(duration / points.length, 0.05) * 3;
      let drawing = false;
      let previousTime = 0;
      for (const point of points) {
        const midi = point.midi_note + point.cents / 100;
        const px = x(point.timestamp_seconds);
        const py = y(midi);
        if (drawing && point.timestamp_seconds - previousTime <= gapLimit) {
          context.lineTo(px, py);
        } else {
          context.moveTo(px, py);
          drawing = true;
        }
        previousTime = point.timestamp_seconds;
      }
      context.stroke();
    };

    draw();
    // Redrawn on resize because the canvas backing store is sized in device
    // pixels; without this the line stays sharp only at the width it was drawn.
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [points, durationSeconds]);

  if (points.length === 0) return null;

  return (
    <div className="rounded-lg border border-border bg-surface-raised p-4">
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={describe(points)}
        className="h-40 w-full [--graph-grid:var(--border)] [--graph-label:var(--muted)] [--graph-line:var(--accent)] sm:h-48"
      />
      <p className="mt-2 text-right text-[10px] text-muted">time →</p>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        Measured pitch over the recording. Gaps are moments with no reliable
        pitch — silence, breath, or a consonant — and are left empty rather than
        joined up.
      </p>
    </div>
  );
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), high);
}

function midiBounds(points: PitchPoint[]): [number, number] {
  let low = Number.POSITIVE_INFINITY;
  let high = Number.NEGATIVE_INFINITY;
  for (const point of points) {
    const midi = point.midi_note + point.cents / 100;
    if (midi < low) low = midi;
    if (midi > high) high = midi;
  }
  low -= MIDI_PADDING;
  high += MIDI_PADDING;
  const shortfall = MIN_MIDI_SPAN - (high - low);
  if (shortfall > 0) {
    low -= shortfall / 2;
    high += shortfall / 2;
  }
  return [low, high];
}

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

function noteName(midi: number): string {
  return `${NOTE_NAMES[((midi % 12) + 12) % 12]}${Math.floor(midi / 12) - 1}`;
}

function describe(points: PitchPoint[]): string {
  const first = points[0];
  const last = points[points.length - 1];
  return (
    `A graph of measured pitch over time, from ${first.note_name} at ` +
    `${first.timestamp_seconds.toFixed(1)} seconds to ${last.note_name} at ` +
    `${last.timestamp_seconds.toFixed(1)} seconds, across ${points.length} measured points.`
  );
}

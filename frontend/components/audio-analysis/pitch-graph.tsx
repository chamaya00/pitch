"use client";

import { useEffect, useRef } from "react";
import { movedStretchDefinition } from "@/lib/audio-analysis-metrics";
import type { PitchPoint, UnstableSection } from "@/types/api";

interface PitchGraphProps {
  points: PitchPoint[];
  durationSeconds: number;
  /**
   * Stretches where the pitch moved more than the threshold allows.
   *
   * Shaded rather than listed, and that is the whole reason they are here at
   * all. `docs/ai.md` withholds this measurement from the model because a
   * timestamped list *reads as a fault list*, and interpreting one safely needs
   * a musical judgement the measurement does not support. On the graph it is
   * not a list: the trace inside each band shows what the number is about, so
   * the reader sees the evidence rather than a verdict — the same argument the
   * key card makes for showing its twelve pitch classes.
   *
   * It was measured, documented and returned from Step 7I onward and reached
   * nobody until Step 11.6, when an audit compared the payload with the screen.
   */
  movedSections?: UnstableSection[];
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
export function PitchGraph({
  points,
  durationSeconds,
  movedSections = [],
}: PitchGraphProps) {
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
      const moved = style.getPropertyValue("--graph-moved").trim() || "#b45309";

      const [low, high] = midiBounds(points);
      const span = high - low;
      const duration = Math.max(durationSeconds, 0.001);

      const plotWidth = Math.max(width - AXIS_WIDTH, 1);
      const x = (seconds: number) => AXIS_WIDTH + (seconds / duration) * plotWidth;
      const y = (midi: number) => height - ((midi - low) / span) * height;

      // The moved stretches go down first, behind everything, so the trace and
      // the gridlines stay fully legible on top of them. A band is where the
      // pitch moved, never a mark against it — a very low alpha rather than a
      // warning colour at full strength, because this is context and not an
      // alert, and the caption says so in words.
      context.save();
      context.globalAlpha = 0.16;
      context.fillStyle = moved;
      for (const section of movedSections) {
        const start = x(clamp(section.start_seconds, 0, duration));
        const end = x(clamp(section.end_seconds, 0, duration));
        // A section shorter than a pixel would vanish; a minimum width keeps a
        // brief one visible without moving where it starts.
        context.fillRect(start, 0, Math.max(end - start, 2), height);
      }
      context.restore();

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
  }, [points, durationSeconds, movedSections]);

  if (points.length === 0) return null;

  return (
    <div className="rounded-lg border border-border bg-surface-raised p-4">
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={describe(points, movedSections)}
        className="h-40 w-full [--graph-grid:var(--border)] [--graph-label:var(--muted)] [--graph-line:var(--accent)] [--graph-moved:var(--warning)] sm:h-48"
      />
      <p className="mt-2 text-right text-[10px] text-muted">time →</p>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        Measured pitch over the recording. Gaps are moments with no reliable
        pitch — silence, breath, or a consonant — and are left empty rather than
        joined up.
      </p>
      {movedSections.length > 0 && (
        <p className="mt-2 text-xs leading-relaxed text-muted">
          The shaded stretches are where the pitch {movedStretchDefinition()}.{" "}
          <strong className="font-medium">
            That is where the pitch moved, not where you went wrong.
          </strong>{" "}
          Vibrato, a slide, a bend and a laugh all land there — and so does an
          octave error in the detector. The line inside each band is what the
          measurement is about; read it rather than the band.
        </p>
      )}
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

function describe(points: PitchPoint[], movedSections: UnstableSection[]): string {
  const first = points[0];
  const last = points[points.length - 1];
  const shape =
    `A graph of measured pitch over time, from ${first.note_name} at ` +
    `${first.timestamp_seconds.toFixed(1)} seconds to ${last.note_name} at ` +
    `${last.timestamp_seconds.toFixed(1)} seconds, across ${points.length} measured points.`;

  if (movedSections.length === 0) return shape;

  // The bands are shading, which assistive technology cannot see. What they
  // encode goes into the description instead, in the same words the caption
  // uses — a picture whose meaning is carried only by colour is a picture some
  // readers do not get.
  const seconds = movedSections.reduce(
    (total, section) => total + (section.end_seconds - section.start_seconds),
    0,
  );
  const one = movedSections.length === 1;
  return (
    `${shape} ${movedSections.length} stretch${one ? "" : "es"} totalling ` +
    `${seconds.toFixed(1)} seconds ${one ? "is" : "are"} shaded, where the pitch ` +
    `${movedStretchDefinition()}. Vibrato, a slide and a bend all land there; it is where ` +
    "the pitch moved, not where anything went wrong."
  );
}

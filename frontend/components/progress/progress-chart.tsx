"use client";

import {
  CHART_HEIGHT,
  CHART_WIDTH,
  axisLabel,
  chartGeometry,
  formatPointDate,
  formatValue,
} from "@/lib/progress";
import type { ProgressSeries } from "@/types/api";

interface ProgressChartProps {
  series: ProgressSeries;
}

/** Room for the axis labels around the plotted area, in viewBox units. */
const PAD_LEFT = 52;
const PAD_RIGHT = 12;
const PAD_TOP = 12;
const PAD_BOTTOM = 28;

/**
 * One metric over time, as an inline SVG.
 *
 * Hand-written rather than pulled from a charting library: the repository has
 * none, this needs a line and some dots, and a dependency would arrive with its
 * own opinions about accessibility and its own bundle.
 *
 * Three properties matter more than the drawing.
 *
 * **The chart is never the only source.** It carries `role="img"` and a summary
 * label, and the table beside it holds every value including the ones that could
 * not be plotted. A reader who cannot see the line loses nothing.
 *
 * **A gap is a gap.** `chartGeometry` returns separate polyline segments, so an
 * unmeasured recording leaves a hole rather than a stroke drawn straight across
 * it as if the value had been interpolated — and no dot is ever placed at the
 * axis because a value was missing.
 *
 * **One unit per chart.** The metric selector switches the whole chart rather
 * than overlaying series, because cents and percentages share no axis.
 */
export function ProgressChart({ series }: ProgressChartProps) {
  const geometry = chartGeometry(series.points);
  const plotWidth = CHART_WIDTH - PAD_LEFT - PAD_RIGHT;
  const plotHeight = CHART_HEIGHT - PAD_TOP - PAD_BOTTOM;

  const place = (x: number, y: number) => ({
    x: PAD_LEFT + (x / CHART_WIDTH) * plotWidth,
    y: PAD_TOP + (y / CHART_HEIGHT) * plotHeight,
  });

  if (geometry.dots.length === 0) {
    return (
      <p className="rounded-xl border border-border bg-surface px-5 py-8 text-center text-sm text-muted">
        No measurements to plot for this yet.
      </p>
    );
  }

  const first = series.points.find((point) => point.value !== null);
  const last = [...series.points].reverse().find((point) => point.value !== null);

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        className="h-auto w-full"
        role="img"
        aria-label={summary(series)}
        preserveAspectRatio="none"
      >
        {/* Axis rails, drawn in the border colour so they recede. */}
        <line
          x1={PAD_LEFT}
          y1={PAD_TOP}
          x2={PAD_LEFT}
          y2={PAD_TOP + plotHeight}
          stroke="var(--border)"
          strokeWidth={1}
        />
        <line
          x1={PAD_LEFT}
          y1={PAD_TOP + plotHeight}
          x2={PAD_LEFT + plotWidth}
          y2={PAD_TOP + plotHeight}
          stroke="var(--border)"
          strokeWidth={1}
        />

        {/* Value labels at the extremes of the scale, so the axis has units. */}
        <text x={PAD_LEFT - 6} y={PAD_TOP + 4} textAnchor="end" className="fill-[var(--muted)] text-[10px]">
          {formatValue(geometry.max, series.unit)}
        </text>
        <text
          x={PAD_LEFT - 6}
          y={PAD_TOP + plotHeight}
          textAnchor="end"
          className="fill-[var(--muted)] text-[10px]"
        >
          {formatValue(geometry.min, series.unit)}
        </text>

        {geometry.segments.map((segment, index) => (
          <polyline
            key={index}
            points={segment
              .map((node) => {
                const at = place(node.x, node.y);
                return `${at.x},${at.y}`;
              })
              .join(" ")}
            fill="none"
            stroke="var(--accent)"
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ))}

        {geometry.dots.map((dot) => {
          const at = place(dot.x, dot.y);
          return (
            <circle
              key={dot.point.recording_id}
              cx={at.x}
              cy={at.y}
              r={3.5}
              fill="var(--accent)"
              vectorEffect="non-scaling-stroke"
            />
          );
        })}

        {first && (
          <text
            x={PAD_LEFT}
            y={CHART_HEIGHT - 8}
            textAnchor="start"
            className="fill-[var(--muted)] text-[10px]"
          >
            {formatPointDate(first.recorded_at)}
          </text>
        )}
        {last && last !== first && (
          <text
            x={PAD_LEFT + plotWidth}
            y={CHART_HEIGHT - 8}
            textAnchor="end"
            className="fill-[var(--muted)] text-[10px]"
          >
            {formatPointDate(last.recorded_at)}
          </text>
        )}
      </svg>
      <figcaption className="mt-2 text-xs text-muted">
        {series.label}, in {axisLabel(series.unit)}. Oldest on the left. Every
        value is also in the table below, including the recordings that could not
        be measured.
      </figcaption>
    </figure>
  );
}

function summary(series: ProgressSeries): string {
  const measured = series.points.filter((point) => point.value !== null);
  if (measured.length === 0) return `${series.label}: nothing measured yet.`;
  const first = measured[0];
  const last = measured[measured.length - 1];
  const gaps = series.points.length - measured.length;
  const gapNote =
    gaps === 0
      ? ""
      : ` ${gaps} recording${gaps === 1 ? "" : "s"} could not be measured and ${
          gaps === 1 ? "is" : "are"
        } shown as a gap.`;
  return (
    `${series.label}, oldest to newest. ${measured.length} measurement` +
    `${measured.length === 1 ? "" : "s"}, from ` +
    `${formatValue(first.value, series.unit)} on ${formatPointDate(first.recorded_at)} ` +
    `to ${formatValue(last.value, series.unit)} on ${formatPointDate(last.recorded_at)}.` +
    `${gapNote} The full values are in the table below.`
  );
}

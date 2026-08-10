"use client";

import {
  fillerSummary,
  NOT_MEASURED,
  paceRows,
  pauseRows,
  type MetricRow,
} from "@/lib/analysis-metrics";
import type { SpeechMetrics } from "@/types/api";

interface AnalysisMetricsProps {
  metrics: SpeechMetrics;
}

/**
 * The measured half of the result.
 *
 * Everything here is arithmetic over the transcript — no model wrote any of
 * it, and the section says so. An unmeasured value reads "Not measured" rather
 * than "0", because those mean different things and the difference is the
 * whole point of the metrics layer.
 */
export function AnalysisMetrics({ metrics }: AnalysisMetricsProps) {
  const fillers = fillerSummary(metrics.filler_words);

  return (
    <section
      aria-labelledby="metrics-heading"
      className="rounded-xl border border-border bg-surface"
    >
      <header className="border-b border-border p-5">
        <h3 id="metrics-heading" className="text-sm font-medium">
          Measured
        </h3>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          Counted and calculated directly from the transcript. No model produced
          these numbers.
        </p>
      </header>

      <MetricGrid rows={paceRows(metrics)} />

      <div className="border-t border-border">
        <MetricGrid rows={pauseRows(metrics)} />
      </div>

      <div className="border-t border-border p-5">
        <h4 className="text-xs font-medium uppercase tracking-wide text-muted">
          Filler words
        </h4>

        {fillers.measured ? (
          <>
            <div className="mt-3 flex flex-wrap gap-x-8 gap-y-3">
              <p className="text-sm">
                <span className="font-mono text-lg tabular-nums">
                  {fillers.hesitations}
                </span>{" "}
                <span className="text-muted">hesitations</span>
              </p>
              <p className="text-sm">
                <span className="font-mono text-lg tabular-nums">
                  {fillers.discourseMarkers}
                </span>{" "}
                <span className="text-muted">discourse markers</span>
              </p>
            </div>

            {fillers.terms.length > 0 && (
              <ul className="mt-3 flex flex-wrap gap-2">
                {fillers.terms.map((item) => (
                  <li
                    key={`${item.category}-${item.term}`}
                    className="rounded-full border border-border px-3 py-1 text-xs"
                  >
                    <span className="break-all">{item.term}</span>
                    <span className="ml-1.5 font-mono tabular-nums text-muted">
                      ×{item.count}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p className="mt-2 text-sm text-muted">{NOT_MEASURED}</p>
        )}

        <p className="mt-3 text-xs leading-relaxed text-muted">{fillers.note}</p>
      </div>
    </section>
  );
}

function MetricGrid({ rows }: { rows: MetricRow[] }) {
  return (
    <dl className="grid grid-cols-2 gap-px bg-border sm:grid-cols-4">
      {rows.map((metric) => (
        <div key={metric.label} className="bg-surface px-5 py-4">
          <dt className="text-xs text-muted">{metric.label}</dt>
          <dd
            className={`mt-1 font-mono text-sm tabular-nums ${
              metric.measured ? "" : "text-muted"
            }`}
          >
            {metric.value}
          </dd>
          {metric.hint && (
            <p className="mt-1.5 font-sans text-xs leading-relaxed text-muted">
              {metric.hint}
            </p>
          )}
        </div>
      ))}
    </dl>
  );
}

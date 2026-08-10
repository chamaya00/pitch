"use client";

import { AnalysisFeedback } from "@/components/analysis/analysis-feedback";
import { AnalysisMetrics } from "@/components/analysis/analysis-metrics";
import { AnalysisTranscript } from "@/components/analysis/analysis-transcript";
import { MockDataBanner } from "@/components/analysis/mock-data-banner";
import type { AnalysisResponse } from "@/types/api";

interface AnalysisResultProps {
  analysis: AnalysisResponse;
}

/**
 * A completed analysis, in the order the reader needs it.
 *
 * Demo warning first when it applies, then what was said, then what was
 * measured, then what a model made of it. There is deliberately no headline
 * score: no composite number exists behind this API, and inventing one would
 * be the fastest way to make everything else look like a grade.
 */
export function AnalysisResult({ analysis }: AnalysisResultProps) {
  return (
    <div className="fade-in space-y-4">
      {analysis.provenance.is_mock && <MockDataBanner />}

      {analysis.transcript && (
        <AnalysisTranscript transcript={analysis.transcript} />
      )}

      {analysis.metrics && <AnalysisMetrics metrics={analysis.metrics} />}

      <AnalysisFeedback
        feedback={analysis.feedback}
        provenance={analysis.provenance.feedback}
      />

      <p className="px-1 text-xs leading-relaxed text-muted">
        This is an automated analysis of one recording. It is not a
        professional, clinical or educational assessment.
      </p>
    </div>
  );
}

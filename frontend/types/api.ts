/** Response shapes returned by the VocalLens API. */

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  environment: string;
}

/** Non-sensitive upload limits, mirroring `PublicConfigResponse`. */
export interface PublicConfig {
  max_audio_size_mb: number;
  max_audio_size_bytes: number;
  max_audio_duration_seconds: number;
  supported_extensions: string[];
}

/** A recording that was accepted, validated and stored. */
export interface Recording {
  recording_id: string;
  original_filename: string;
  format: string;
  duration_seconds: number;
  sample_rate: number;
  channels: number;
  size_bytes: number;
  bits_per_sample: number | null;
  created_at: string;
}

/**
 * Error envelope used by the API for handled failures. Clients branch on
 * `error_code`; `message` is server-authored prose safe to show to a user.
 */
export interface ApiErrorBody {
  status: "failed";
  error_code: string;
  message: string;
}

/* --- Analysis -------------------------------------------------------------
 *
 * Mirrors `backend/app/schemas/analysis.py`. Two rules carry over from the
 * backend and must survive into the UI:
 *
 * `null` is not zero. Every optional number below is `null` when the recording
 * did not support measuring it. Defaulting one to `0` while rendering would
 * turn "we could not measure this" into "we measured this and it was none" —
 * which is why none of these are widened to `number` here.
 *
 * Measured and generated are different things. `metrics` is arithmetic over the
 * transcript; `feedback` is a language model's prose about those numbers. They
 * are separate objects on purpose and must stay separate on screen.
 */

export type AnalysisStatus =
  | "pending"
  | "transcribing"
  | "analyzing"
  | "completed"
  | "failed";

/** Which provider produced one piece of content. Never carries a credential. */
export interface Provenance {
  provider: string;
  model: string | null;
  is_mock: boolean;
}

export interface AnalysisProvenance {
  transcription: Provenance | null;
  feedback: Provenance | null;
  /** `true` if any content in this analysis came from a mock provider. */
  is_mock: boolean;
}

export interface TranscriptWord {
  text: string;
  /** `null` when the provider returned no timing — not a word starting at 0. */
  start_seconds: number | null;
  end_seconds: number | null;
}

export interface Transcript {
  text: string;
  words: TranscriptWord[];
  language: string | null;
  audio_duration_seconds: number | null;
  /** When `false`, filler statistics are absent because they are unmeasurable. */
  includes_disfluencies: boolean;
}

export type FillerCategory = "hesitation" | "discourse_marker";

export interface FillerTermCount {
  term: string;
  category: FillerCategory | string;
  count: number;
}

export interface FillerWordStats {
  hesitation_count: number;
  discourse_marker_count: number;
  total_count: number;
  by_term: FillerTermCount[];
}

export type DurationSource = "word_timings" | "recording_duration";

/** Deterministic measurements. Only `word_count` is always available. */
export interface SpeechMetrics {
  word_count: number;
  duration_source: DurationSource | string | null;
  speaking_duration_seconds: number | null;
  words_per_minute: number | null;
  articulation_rate_wpm: number | null;
  pause_threshold_seconds: number | null;
  /** `null`: no timings to detect pauses from. `0`: measured, none found. */
  pause_count: number | null;
  total_pause_seconds: number | null;
  longest_pause_seconds: number | null;
  mean_pause_seconds: number | null;
  /** `null` when the transcript is not verbatim. Never means "none said". */
  filler_words: FillerWordStats | null;
}

/** Language-model prose about the metrics. Not a measurement. */
export interface Feedback {
  summary: string;
  strengths: string[];
  areas_to_improve: string[];
  next_action: string;
}

export interface AnalysisResponse {
  analysis_id: string;
  recording_id: string;
  status: AnalysisStatus;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  /** Set only when `status` is `failed`. */
  error_code: string | null;
  transcript: Transcript | null;
  metrics: SpeechMetrics | null;
  /** May be `null` on a completed analysis — that is not a failure. */
  feedback: Feedback | null;
  provenance: AnalysisProvenance;
}

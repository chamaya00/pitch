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

/* --- Deterministic audio analysis ------------------------------------------
 *
 * Mirrors `backend/app/schemas/audio_analysis.py`. Kept apart from the speech
 * types above because the two describe different measurements of the same
 * recording: one counts words, the other measures the signal. Nothing in the
 * UI combines them, and there is no type here that could hold a combined score.
 *
 * Every optional field is `number | null` for the same reason as everywhere
 * else: `null` means the signal did not support the measurement, and rendering
 * it as `0` would state something the server did not.
 */

export type AudioAnalysisStatus = "pending" | "analyzing" | "completed" | "failed";

/** The parameters a result was measured with. Published so it stays readable. */
export interface AudioAnalysisSettings {
  sample_rate_hz: number;
  frame_length_samples: number;
  hop_length_samples: number;
  min_frequency_hz: number;
  max_frequency_hz: number;
  clarity_threshold: number;
  silence_rms: number;
}

/** The range **detected in this recording**. Never a physiological limit. */
export interface DetectedRange {
  lowest_frequency_hz: number;
  highest_frequency_hz: number;
  lowest_note: string;
  highest_note: string;
  semitone_span: number;
}

export interface UnstableSection {
  start_seconds: number;
  end_seconds: number;
  cents_std: number;
}

export interface PitchStabilityMetrics {
  voiced_ratio: number;
  total_frames: number;
  voiced_frames: number;
  mean_cents_deviation: number | null;
  mean_abs_cents_deviation: number | null;
  cents_std: number | null;
  semitone_variance: number | null;
  /** Share of voiced frames within 25 cents of a note. Not a grade. */
  in_tune_ratio: number | null;
  unstable_sections: UnstableSection[];
}

/** Amplitude measurements. Not LUFS. */
export interface LoudnessMetrics {
  rms: number;
  peak: number;
  dynamic_range_db: number | null;
  crest_factor_db: number | null;
  clipped_sample_ratio: number;
}

/** Raw spectral shape. No timbre label is derived from these, anywhere. */
export interface SpectralMetrics {
  centroid_hz: number;
  bandwidth_hz: number;
  rolloff_hz: number;
  zero_crossing_rate: number;
  flatness: number;
}

export interface AudioSummary {
  duration_seconds: number;
  settings: AudioAnalysisSettings;
  /** `null` when no pitch was held long enough to define a range. */
  range: DetectedRange | null;
  stability: PitchStabilityMetrics;
  loudness: LoudnessMetrics;
  spectral: SpectralMetrics | null;
}

export interface AudioAnalysisResponse {
  audio_analysis_id: string;
  recording_id: string;
  status: AudioAnalysisStatus;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  summary: AudioSummary | null;
  /** The timeline lives on its own path; this is only its length. */
  pitch_point_count: number;
}

/** One voiced frame. Unvoiced frames are omitted from the timeline entirely. */
export interface PitchPoint {
  timestamp_seconds: number;
  frequency_hz: number;
  midi_note: number;
  note_name: string;
  cents: number;
  confidence: number;
}

export interface PitchTimeline {
  recording_id: string;
  audio_analysis_id: string;
  total_points: number;
  returned_points: number;
  /** 1 when every point is present; n when every n-th was taken. */
  decimation: number;
  points: PitchPoint[];
}

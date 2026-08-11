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

/** How much of the pitched time was spent on one musical note. */
export interface NoteSummary {
  midi_note: number;
  note_name: string;
  duration_seconds: number;
  /** Share of *pitched* time, not of the recording. These sum to 100. */
  percentage_of_voiced_time: number;
  frame_count: number;
  average_cents: number;
  mean_abs_cents: number;
  /** Share of this note's frames within `in_tune_cents` of it. Not a grade. */
  in_tune_ratio: number;
}

export interface NoteBreakdown {
  recording_id: string;
  audio_analysis_id: string;
  voiced_seconds: number;
  total_frames: number;
  /** The threshold `in_tune_ratio` was measured against, in cents. */
  in_tune_cents: number;
  /** Longest first, lower note first on a tie. Empty means none were detected. */
  notes: NoteSummary[];
}

/** Where a recording's audio feedback has got to. */
export type AudioFeedbackStatus =
  | "not_requested"
  | "generating"
  | "completed"
  | "failed";

/**
 * A language model's interpretation of the measured audio.
 *
 * Note what has no field here: a score, a grade, a timbre label. Nothing in
 * this type could hold one, which is the cheapest way to guarantee none is
 * shown.
 */
export interface AudioFeedback {
  summary: string;
  strengths: string[];
  areas_to_improve: string[];
  pitch_observations: string[];
  range_observations: string[];
  note_observations: string[];
  audio_observations: string[];
  exercises: string[];
  practice_plan: string[];
  provenance: Provenance;
}

export interface AudioFeedbackState {
  recording_id: string;
  audio_analysis_id: string;
  status: AudioFeedbackStatus;
  error_code: string | null;
  feedback: AudioFeedback | null;
}

/* --- Recording history ----------------------------------------------------- */

/**
 * One recording in an owner's history, with how far its analyses got.
 *
 * The statuses are nullable and the distinction is load-bearing: `null` means
 * that analysis has **never been run**, which is not `pending` and not a
 * failure. Rendering the two the same way would tell somebody their recording
 * failed when nobody ever asked for it to be analysed.
 *
 * No measurements here on purpose — a list is not the place for a pitch
 * timeline, and comparing two recordings whose conditions nobody controlled is
 * exactly the reading this type should not invite.
 */
export interface RecordingHistoryItem {
  recording: Recording;
  speech_status: AnalysisStatus | null;
  audio_status: AudioAnalysisStatus | null;
  feedback_status: AudioFeedbackStatus | null;
  last_analysed_at: string | null;
}

export interface RecordingHistory {
  items: RecordingHistoryItem[];
  count: number;
  limit: number;
}

/* --- Recording comparison -------------------------------------------------- */

/** What a delta is expressed in. `percentage_points` is never `percent`. */
export type MetricUnit =
  | "percentage_points"
  | "cents"
  | "semitones"
  | "seconds";

/**
 * Whether a direction of change means anything, and exactly what.
 *
 * `neutral` covers recording length, pitched time, voiced share and the
 * detected range: those differences have no better end, and the UI must not
 * imply one. The two directed values are defined relative to equal-tempered
 * pitch, never to singing quality.
 */
export type MetricDirection =
  | "higher_is_nearer_the_note"
  | "lower_is_nearer_the_note"
  | "lower_is_steadier"
  | "neutral";

/** Which recordings supported a measurement. Anything but `both` means no delta. */
export type MetricAvailability = "both" | "left_only" | "right_only" | "neither";

export interface ComparedMetric {
  key: string;
  label: string;
  unit: MetricUnit;
  direction: MetricDirection;
  /** `null` means the recording did not support the measurement. Never zero. */
  left: number | null;
  right: number | null;
  /** `right - left`, in `unit`. `null` whenever either side is missing. */
  delta: number | null;
  availability: MetricAvailability;
}

export type NotePresence = "both" | "left_only" | "right_only";

export interface ComparedNoteEntry {
  duration_seconds: number;
  percentage_of_voiced_time: number;
  frame_count: number;
  mean_abs_cents: number;
  in_tune_ratio: number;
}

export interface ComparedNote {
  midi_note: number;
  note_name: string;
  presence: NotePresence;
  /** `null` means the note was never sung — not sung for zero seconds. */
  left: ComparedNoteEntry | null;
  right: ComparedNoteEntry | null;
  share_delta_points: number | null;
  duration_delta_seconds: number | null;
  mean_abs_cents_delta: number | null;
}

/** Why one recording cannot take part. `not_found` also covers "not yours". */
export type ComparisonSideStatus =
  | "ready"
  | "not_found"
  | "analysis_missing"
  | "analysis_in_progress"
  | "analysis_failed"
  | "insufficient_pitch_signal";

export interface ComparisonSide {
  recording_id: string;
  status: ComparisonSideStatus;
  original_filename: string | null;
  created_at: string | null;
  duration_seconds: number | null;
  audio_format: string | null;
  error_code: string | null;
  /** Detected in this recording only. `null` when no range was established. */
  lowest_note: string | null;
  highest_note: string | null;
}

/**
 * Two recordings' measurements, side by side.
 *
 * Note what has no field here: an overall figure. There is no score in this
 * product, and a comparison does not introduce the first one.
 */
export interface RecordingComparison {
  left: ComparisonSide;
  right: ComparisonSide;
  comparable: boolean;
  metrics: ComparedMetric[];
  notes: ComparedNote[];
  caveats: string[];
}

/* --- Progress over time ---------------------------------------------------- */

/** The measurements that may be plotted over time. */
export type ProgressMetricKey =
  | "in_tune_ratio"
  | "mean_abs_cents_deviation"
  | "cents_std"
  | "semitone_span"
  | "voiced_ratio"
  | "voiced_seconds"
  | "duration_seconds";

/**
 * Whether a recording contributed a number, and if not, why.
 *
 * Only `measured` carries a value. The other two are gaps in the line — never
 * zeroes, and never omitted from the table, because a recording belongs in its
 * own history whether or not it could be measured.
 */
export type PointStatus = "measured" | "not_measured" | "not_eligible";

export interface ProgressPoint {
  recording_id: string;
  recorded_at: string;
  status: PointStatus;
  value: number | null;
}

/**
 * How the latest measured value compares with the previous measured one.
 *
 * `insufficient_data` is not `unchanged`: one means there is no basis for
 * saying, the other is a claim.
 */
export type ObservationDirection =
  | "higher"
  | "lower"
  | "unchanged"
  | "insufficient_data";

export interface LatestObservation {
  direction: ObservationDirection;
  latest: number | null;
  previous: number | null;
  delta: number | null;
  latest_recording_id: string | null;
  previous_recording_id: string | null;
}

export interface ProgressSeries {
  metric: ProgressMetricKey;
  label: string;
  unit: MetricUnit;
  direction: MetricDirection;
  points: ProgressPoint[];
  observation: LatestObservation;
  measured_count: number;
}

export interface ProgressRecording {
  recording_id: string;
  recorded_at: string;
  original_filename: string;
  duration_seconds: number;
  audio_format: string;
  analysed: boolean;
  lowest_note: string | null;
  highest_note: string | null;
}

/**
 * How much measured history exists, and therefore what may honestly be shown.
 *
 * `pair` is two measurements — a comparison, emphatically not a trend.
 */
export type HistoryDepth = "empty" | "single" | "pair" | "series";

/**
 * An owner's recorded measurements over time.
 *
 * Note what has no field here: a score, a level, a rank, or any claim that the
 * singing changed.
 */
export interface ProgressHistory {
  series: ProgressSeries[];
  recordings: ProgressRecording[];
  depth: HistoryDepth;
  limit: number;
}

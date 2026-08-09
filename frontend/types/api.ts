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

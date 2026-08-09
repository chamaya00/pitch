/** Response shapes returned by the VocalLens API. */

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  environment: string;
}

/**
 * Error envelope used by the API for handled failures (e.g. an analysis that
 * could not find enough pitch signal). Kept here from Phase 0 so both sides
 * agree on the contract before it is first used.
 */
export interface ApiErrorBody {
  status: "failed";
  error_code: string;
  message: string;
}

/**
 * Client-visible configuration.
 *
 * `NEXT_PUBLIC_API_URL` is inlined at build time, so it must only ever contain
 * non-secret values (the public base URL of the VocalLens API).
 */
export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

export const API_V1 = `${API_BASE_URL}/api/v1`;

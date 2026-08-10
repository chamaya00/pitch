/**
 * Display formatting helpers.
 *
 * Pure and dependency-free so they can be tested with `node --test`.
 */

const UNITS = ["B", "KB", "MB", "GB"] as const;

/** Human-readable file size, e.g. `2.4 MB`. Uses 1024-based units. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${Math.round(bytes)} B`;

  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < UNITS.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const decimals = value >= 100 ? 0 : 1;
  return `${value.toFixed(decimals)} ${UNITS[unitIndex]}`;
}

/** Duration as `m:ss`, or `s.s s` below a minute. */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;

  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds - minutes * 60);
  if (remainder === 60) return `${minutes + 1}:00`;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

/** Sample rate in kHz, e.g. `44.1 kHz`. */
export function formatSampleRate(hertz: number): string {
  if (!Number.isFinite(hertz) || hertz <= 0) return "—";
  const kilohertz = hertz / 1000;
  return `${Number.isInteger(kilohertz) ? kilohertz : kilohertz.toFixed(1)} kHz`;
}

/** Channel count as a word, since "1" alone reads poorly in a summary. */
export function formatChannels(channels: number): string {
  if (channels === 1) return "Mono";
  if (channels === 2) return "Stereo";
  if (!Number.isFinite(channels) || channels <= 0) return "—";
  return `${channels} channels`;
}

/** Duration limit phrased for a sentence, e.g. `5 minutes`. */
export function formatDurationLimit(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds % 60 === 0) {
    const minutes = seconds / 60;
    return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  }
  return `${seconds} seconds`;
}

/**
 * A recording clock: `mm:ss`, or `h:mm:ss` past an hour.
 *
 * Distinct from `formatDuration`, which reads as prose ("29.6s") and is right
 * for a measured length in a results table. A timer ticking while someone
 * records needs fixed width and a familiar shape, so it does not reflow on
 * every second and can be read at a glance.
 */
export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const whole = Math.floor(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const remainder = whole % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(remainder)}`
    : `${pad(minutes)}:${pad(remainder)}`;
}

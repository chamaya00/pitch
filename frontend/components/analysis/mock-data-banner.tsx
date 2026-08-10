/**
 * Says plainly that what follows is not real analysis.
 *
 * Rendered whenever `provenance.is_mock` is true — at the top of the result,
 * in the warning colour, in plain words. Not a tooltip, not a footnote: a user
 * must not be able to read these numbers as measurements of their speech
 * without also reading this.
 *
 * The body is a prop because the two places this appears are standing in for
 * different things: the speech result has no real transcript, while the audio
 * feedback card has real measurements and only borrowed prose. Saying "this was
 * not transcribed" over a genuine measurement would be its own small untruth.
 */
interface MockDataBannerProps {
  children?: React.ReactNode;
}

export function MockDataBanner({ children }: MockDataBannerProps) {
  return (
    <div
      role="note"
      className="flex items-start gap-3 rounded-xl border border-warning/50 bg-warning/10 p-4"
    >
      <span
        aria-hidden
        className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-warning/20 text-warning"
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
        >
          <path d="M12 8v5" />
          <path d="M12 17h.01" />
        </svg>
      </span>
      <div className="min-w-0">
        <p className="text-sm font-semibold text-warning">
          Demo data — not real analysis
        </p>
        <p className="mt-1 text-sm leading-relaxed text-muted">
          {children ?? (
            <>
              No speech-to-text provider is configured, so this recording was not
              transcribed. The transcript, numbers and feedback below come from a
              development stand-in and describe nothing about your voice.
            </>
          )}
        </p>
      </div>
    </div>
  );
}

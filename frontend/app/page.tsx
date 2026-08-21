import { BackendStatus } from "@/components/backend-status";
import { CapturePanel } from "@/components/capture-panel";
import { RecordingHistory } from "@/components/history/recording-history";
import { IdentityPanel } from "@/components/identity/identity-panel";
import { ProgressPanel } from "@/components/progress/progress-panel";

/**
 * What the product measures, as a first-time reader meets it.
 *
 * **This list is the one thing on the page that goes stale silently**, and it
 * had. Until Step 11.6 it named five things, all of them from the speech half
 * and the live meter, and said nothing about pitch, detected range, note
 * breakdown, musical key, comparison or progress — half the product, and the
 * half the plan's own home page was written around. Nothing failed, because
 * copy has no test; the audit that went looking found it.
 *
 * The rule that keeps it honest: **every entry names something a reader can
 * reach today**, and each says what it will not claim. There is no entry for
 * anything unbuilt.
 */
const MEASURED = [
  {
    title: "Pitch, range and key",
    body: "Per-frame pitch as a note and a cents deviation, the range the recording contained, how steady the pitch was, and the key those notes best fit. The range is what you sang here — never the limit of your voice.",
  },
  {
    title: "Where the notes went",
    body: "Every note the recording spent time on, how long it was held, and how far from the note it sat. A slide leaves a trace on the notes it passed through, and that is shown rather than smoothed away.",
  },
  {
    title: "Speaking rate and pauses",
    body: "Words per minute across the recording, articulation rate with pauses removed, how many gaps cleared the threshold and how long the longest one ran.",
  },
  {
    title: "Filler words",
    body: "Hesitations counted separately from ordinary words that only sometimes act as fillers — and omitted entirely when the transcript cannot support the count.",
  },
  {
    title: "Transcript",
    body: "What was said, with per-word timings wherever the provider supplies them.",
  },
  {
    title: "Loudness and spectrum",
    body: "Level, peak, dynamic range and clipping, plus the raw spectral characteristics of the signal. These are not LUFS, and no word like “bright” or “breathy” is derived from them.",
  },
  {
    title: "Live vocal practice",
    body: "While you record: the note you are on, how flat or sharp in cents, a scrolling pitch trace, the range you have covered and the key you seem to be in. Computed in your browser — the audio never leaves the page while you sing.",
  },
  {
    title: "Over time, and side by side",
    body: "Your recordings charted, and any two of them compared. Deterministic arithmetic throughout: no grade, no level and no trend line.",
  },
  {
    title: "Whether a song fits",
    body: "Describe a song by its lowest and highest note and see how much of it falls inside the range a recording covered, and what shift would bring the rest in. The song's numbers are the ones you type; the recording's were measured.",
  },
];

export default function Home() {
  return (
    <div className="mx-auto max-w-3xl px-6">
      <section className="pt-16 pb-10 sm:pt-24 sm:pb-12">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          Hear how you sound.
        </h1>
        <p className="mt-4 max-w-xl text-lg leading-relaxed text-muted">
          Record straight from your microphone, or upload a file. VocalLens
          measures it two ways — your pitch, range, steadiness and key, and what
          you said and how you paced it — and shows you the numbers behind
          both.
        </p>
      </section>

      <BackendStatus />

      <CapturePanel />

      <div className="mt-16">
        <RecordingHistory />
      </div>

      <div className="mt-16">
        <ProgressPanel />
      </div>

      <div className="mt-16">
        <IdentityPanel />
      </div>

      <section className="border-t border-border py-14 mt-16">
        <h2 className="text-sm font-medium uppercase tracking-wide text-muted">
          What VocalLens measures
        </h2>
        <ul className="mt-6 grid gap-4 sm:grid-cols-2">
          {MEASURED.map((metric) => (
            <li
              key={metric.title}
              className="rounded-lg border border-border bg-surface p-5"
            >
              <h3 className="font-medium">{metric.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">
                {metric.body}
              </p>
            </li>
          ))}
        </ul>
        <p className="mt-6 text-sm leading-relaxed text-muted">
          Every number shown in VocalLens is measured — from the audio signal,
          or counted from the transcript. AI is used only to explain those
          measurements: never to invent one, and never to produce a score. A
          measurement that could not be taken says so rather than showing a
          zero, and there is no combined figure anywhere, because nothing here
          measures how to weight one against another.
        </p>
      </section>
    </div>
  );
}

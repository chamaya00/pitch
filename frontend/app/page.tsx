import { BackendStatus } from "@/components/backend-status";
import { UploadPanel } from "@/components/upload/upload-panel";

const PLANNED_METRICS = [
  {
    title: "Pitch over time",
    body: "Fundamental frequency per frame, converted to MIDI notes and cents deviation.",
  },
  {
    title: "Vocal range",
    body: "Lowest and highest reliably detected notes, and the span in semitones.",
  },
  {
    title: "Pitch stability",
    body: "Voiced ratio, pitch variance and average cents deviation across the recording.",
  },
  {
    title: "Loudness & timbre",
    body: "RMS, peak amplitude and spectral features measured directly from the signal.",
  },
];

export default function Home() {
  return (
    <div className="mx-auto max-w-3xl px-6">
      <section className="pt-16 pb-10 sm:pt-24 sm:pb-12">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          Understand your voice.
        </h1>
        <p className="mt-4 max-w-xl text-lg leading-relaxed text-muted">
          Upload a recording and discover your pitch, range, stability and vocal
          patterns.
        </p>
      </section>

      <BackendStatus />

      <UploadPanel />

      <section className="border-t border-border py-14 mt-16">
        <h2 className="text-sm font-medium uppercase tracking-wide text-muted">
          What VocalLens will measure
        </h2>
        <ul className="mt-6 grid gap-4 sm:grid-cols-2">
          {PLANNED_METRICS.map((metric) => (
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
          Every number shown in VocalLens comes from deterministic audio
          analysis. AI is used only to explain those measurements — never to
          invent them.
        </p>
      </section>
    </div>
  );
}

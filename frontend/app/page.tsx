import { BackendStatus } from "@/components/backend-status";
import { Button } from "@/components/ui/button";

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
    <div className="mx-auto max-w-5xl px-6">
      <section className="py-20 sm:py-28">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          Understand your voice.
        </h1>
        <p className="mt-4 max-w-xl text-lg text-muted">
          Upload a recording and discover your pitch, range, stability and vocal
          patterns.
        </p>

        <div className="mt-8 flex flex-wrap gap-3">
          <Button disabled aria-describedby="phase-note">
            Upload audio
          </Button>
          <Button variant="secondary" disabled aria-describedby="phase-note">
            Record voice
          </Button>
        </div>

        <p id="phase-note" className="mt-3 text-sm text-muted">
          Audio upload and recording arrive in the next phase. The project
          foundation is in place — nothing here is simulated.
        </p>

        <div className="mt-8">
          <BackendStatus />
        </div>
      </section>

      <section className="border-t border-border py-14">
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
        <p className="mt-6 max-w-2xl text-sm leading-relaxed text-muted">
          Every number shown in VocalLens comes from deterministic audio
          analysis. AI is used only to explain those measurements — never to
          invent them.
        </p>
      </section>
    </div>
  );
}

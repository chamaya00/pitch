import { BackendStatus } from "@/components/backend-status";
import { UploadPanel } from "@/components/upload/upload-panel";

const MEASURED = [
  {
    title: "Speaking rate",
    body: "Words per minute across the recording, and articulation rate with pauses removed.",
  },
  {
    title: "Pauses",
    body: "How many gaps cleared the threshold, how long they ran, and the longest one.",
  },
  {
    title: "Filler words",
    body: "Hesitations counted separately from ordinary words that only sometimes act as fillers — and omitted entirely when the transcript cannot support the count.",
  },
  {
    title: "Transcript",
    body: "What was said, with per-word timings wherever the provider supplies them.",
  },
];

export default function Home() {
  return (
    <div className="mx-auto max-w-3xl px-6">
      <section className="pt-16 pb-10 sm:pt-24 sm:pb-12">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          Hear how you speak.
        </h1>
        <p className="mt-4 max-w-xl text-lg leading-relaxed text-muted">
          Upload a recording to see it transcribed, measure your pace and
          pauses, and get feedback on how it lands.
        </p>
      </section>

      <BackendStatus />

      <UploadPanel />

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
          Every number shown in VocalLens is counted from the transcript. AI is
          used only to explain those measurements — never to invent them, and
          never to produce a score. A measurement that could not be taken says
          so rather than showing a zero.
        </p>
      </section>
    </div>
  );
}

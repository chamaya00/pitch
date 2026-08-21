import Link from "next/link";

export function SiteHeader() {
  return (
    <header className="border-b border-border">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <Link href="/" className="flex items-center gap-2 font-medium">
          <span
            aria-hidden
            className="inline-block h-4 w-1 rounded-full bg-accent"
          />
          VocalLens
        </Link>
        <p className="hidden text-sm text-muted sm:block">
          Measure how you sound. Understand what it means.
        </p>
      </div>
    </header>
  );
}

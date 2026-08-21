import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { SiteHeader } from "@/components/site-header";
import { ServiceWorker } from "@/components/service-worker";
import { SiteFooter } from "@/components/site-footer";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

/**
 * Rendered per request, and only because of the Content-Security-Policy.
 *
 * `proxy.ts` mints a nonce per request and Next.js stamps it onto every script
 * element as it renders the page. A *prerendered* page is built before that
 * nonce exists, so it ships with none — measured on this application: with the
 * route static, the policy was served correctly and Chromium then refused all
 * ten of the page's own script elements, leaving inert HTML. The nonce and the
 * full-route cache cannot both be had.
 *
 * What that costs here is small and was measured rather than assumed (see
 * `docs/architecture.md`): every page in this product is an app shell whose
 * content is fetched in the browser, so the render this gives up produced no
 * data, called nothing and read nothing.
 */
export const dynamic = "force-dynamic";

/**
 * The colour a platform paints its own chrome with — the address bar, and the
 * area behind an installed window's status bar.
 *
 * Two values rather than one, because this product has two genuine themes and a
 * single theme colour would leave one of them with a strip of the other's
 * background above it. This is the *page*'s colour and can vary; the manifest's
 * `theme_color` is read once at install time and cannot, which is why they are
 * set in different places and do not have to agree.
 *
 * Both are the theme's own `--background` token, so the chrome continues the
 * page instead of framing it.
 */
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f7f8" },
    { media: "(prefers-color-scheme: dark)", color: "#0b0b0f" },
  ],
};

export const metadata: Metadata = {
  title: "VocalLens — Hear how you sound",
  description:
    "Record or upload, and see it measured: pitch, detected range, steadiness and key, plus the transcript, pace and pauses behind what you said.",
  // The icon iOS uses for a home-screen launcher. It ignores the manifest's
  // icon list, so an app that only listed them there installs with a screenshot
  // of the page as its icon.
  appleWebApp: { capable: true, title: "VocalLens", statusBarStyle: "black-translucent" },
  icons: { apple: "/icons/apple-touch-icon.png" },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="font-sans min-h-full flex flex-col">
        <SiteHeader />
        <main className="flex-1">{children}</main>
        <SiteFooter />
        <ServiceWorker />
      </body>
    </html>
  );
}

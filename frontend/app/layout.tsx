import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { SiteHeader } from "@/components/site-header";
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

export const metadata: Metadata = {
  title: "VocalLens — Hear how you speak",
  description:
    "Upload a recording to see it transcribed, measure your pace and pauses, and get feedback on how it lands.",
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
      </body>
    </html>
  );
}

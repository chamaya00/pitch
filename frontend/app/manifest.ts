import type { MetadataRoute } from "next";

/**
 * What a browser needs to install VocalLens to a home screen or a dock.
 *
 * **Installing changes how the app is launched, not what it measures.** There
 * is no separate "app version" of anything here: the installed window runs the
 * same page, talks to the same API and computes the same numbers. What it gains
 * is a launcher icon, its own window without browser chrome, and the static
 * assets already cached by the service worker.
 *
 * Two fields are worth their reasoning.
 *
 * `display: "standalone"` rather than `"fullscreen"`. Fullscreen hides the
 * status bar, and this is an app somebody records into — they need to see the
 * clock and their battery while a take is running. `"minimal-ui"` would keep a
 * reload button, which is closer to a browser than to an app.
 *
 * `background_color` matches the icon's own background and the dark theme's
 * surface, so the splash screen a platform paints from these two values is a
 * seamless continuation of the icon rather than a white flash before a dark
 * page. It is deliberately **not** varied by colour scheme: a manifest holds one
 * value, platforms read it at install time, and a light-mode splash that
 * disagreed with the icon would look broken rather than adaptive. The *page*
 * theme colour does vary by scheme — see `viewport` in `app/layout.tsx`.
 *
 * Nothing is listed here that the product does not have. No screenshots, no
 * shortcuts, no share target, no protocol handlers: each of those is a promise
 * the platform will render, and an empty or invented one is worse than its
 * absence.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "VocalLens",
    short_name: "VocalLens",
    description:
      "Record or upload, and see it measured: pitch, detected range, steadiness and key, plus the transcript, pace and pauses behind what you said.",
    start_url: "/",
    // The whole product is one page, so the scope is the origin. A narrower
    // scope would put the app's own links outside it and open them in a browser.
    scope: "/",
    display: "standalone",
    background_color: "#0b0b0f",
    theme_color: "#0b0b0f",
    icons: [
      {
        src: "/icons/icon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icons/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      {
        // Drawn separately rather than reusing the icon above: a maskable slot
        // may be cropped to a circle, and only the middle 80% survives it.
        src: "/icons/maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}

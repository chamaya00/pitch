"use client";

import { useEffect } from "react";

/**
 * Registers the service worker that makes VocalLens installable.
 *
 * Renders nothing. It exists as a component rather than as a script tag so it
 * is covered by the same CSP nonce as every other script the app runs — a bare
 * inline script would need its own allowance, and this policy deliberately has
 * none to give.
 *
 * **Not registered in development.** `next dev` serves modules that change
 * under the page, and a worker sitting in front of them turns an edit into a
 * puzzle about which copy is being served. Production is where a worker is
 * worth having and where it is tested; `npm run build && npm start` exercises
 * it exactly as a deployment does.
 *
 * **Registered on `load`.** Registration competes for bandwidth with the
 * resources the first render actually needs, and the worker does nothing for
 * the visit that installs it — its whole value is to the next one.
 *
 * A failure here is deliberately quiet. A browser without service workers, a
 * private window that refuses to register one, or an insecure origin are all
 * states in which the app works and only the installability is missing. There
 * is nothing for a user to do about any of them, so there is nothing to tell
 * them.
 */
export function ServiceWorker() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;

    const register = () => {
      void navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    };

    if (document.readyState === "complete") {
      register();
      return;
    }
    window.addEventListener("load", register);
    return () => window.removeEventListener("load", register);
  }, []);

  return null;
}

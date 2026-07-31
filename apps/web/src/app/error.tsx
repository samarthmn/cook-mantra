"use client";

import { useEffect } from "react";

import { AppHeader } from "@/components/layout/AppHeader";

export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="app-shell">
      <AppHeader />
      <main className="page-main">
        <section className="job-shell" aria-labelledby="error-title">
          <p className="kicker">Something went wrong</p>
          <h1 className="screen-title" id="error-title">
            The kitchen hit a snag.
          </h1>
          <p className="screen-intro">
            Your browser can retry this screen. If the problem continues, start a fresh
            session from the Cook Mantra home page.
          </p>
          <hr className="section-rule" />
          <button className="btn btn-primary btn-lg" type="button" onClick={reset}>
            Try this screen again
          </button>
        </section>
      </main>
    </div>
  );
}

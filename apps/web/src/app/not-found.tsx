import Link from "next/link";

import { AppHeader } from "@/components/layout/AppHeader";

export default function NotFound() {
  return (
    <div className="app-shell">
      <AppHeader />
      <main className="page-main">
        <section className="job-shell" aria-labelledby="not-found-title">
          <p className="kicker">404 — Not found</p>
          <h1 className="screen-title" id="not-found-title">
            This page isn&apos;t on the menu.
          </h1>
          <p className="screen-intro">
            Go back to Cook Mantra and start with the ingredients you have.
          </p>
          <hr className="section-rule" />
          <Link className="btn btn-primary btn-lg" href="/">
            Back to Cook Mantra
          </Link>
        </section>
      </main>
    </div>
  );
}

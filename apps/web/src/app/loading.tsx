import { AppHeader } from "@/components/layout/AppHeader";

export default function Loading() {
  return (
    <div className="app-shell">
      <AppHeader />
      <main className="page-main" aria-live="polite">
        <section className="job-shell">
          <p className="kicker">Cook Mantra</p>
          <h1 className="screen-title-job">Opening the kitchen</h1>
          <hr className="section-rule" />
          <div className="job-row" data-status="running">
            <span className="job-status job-status-running" aria-label="loading" />
            <div className="job-content">
              <div className="job-label">Loading your workspace</div>
              <div className="job-detail">This should only take a moment.</div>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

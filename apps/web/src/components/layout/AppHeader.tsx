import Link from "next/link";

import { ThemeToggle } from "@/components/ui/ThemeToggle";

export function AppHeader() {
  return (
    <header className="app-header">
      <Link className="app-brand" href="/">
        <span aria-hidden="true" className="app-brand-mark" />
        <span>COOK MANTRA</span>
      </Link>
      <nav aria-label="Application" className="app-header-actions">
        <Link className="app-header-link" href="/design-guide">
          Design guide
        </Link>
        <ThemeToggle />
      </nav>
    </header>
  );
}

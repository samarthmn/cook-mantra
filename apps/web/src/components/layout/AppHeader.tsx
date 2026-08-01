import Image from "next/image";
import Link from "next/link";

import { Bookmark } from "lucide-react";

import { ThemeToggle } from "@/components/ui/ThemeToggle";

interface AppHeaderProps {
  savedCount?: number;
  savedActive?: boolean;
  onOpenSaved?: () => void;
}

export function AppHeader({
  savedCount = 0,
  savedActive = false,
  onOpenSaved,
}: AppHeaderProps = {}) {
  return (
    <header className="app-header">
      <Link className="app-brand" href="/">
        <Image
          alt=""
          aria-hidden="true"
          className="app-brand-mark"
          height={24}
          priority
          src="/brand/cook-mantra-mark.png"
          width={24}
        />
        <span>COOK MANTRA</span>
      </Link>
      <nav aria-label="Application" className="app-header-actions">
        {onOpenSaved ? (
          <button
            aria-label={
              savedCount > 0 ? `Saved recipes, ${savedCount} saved` : "Saved recipes"
            }
            aria-current={savedActive ? "page" : undefined}
            className="btn btn-ghost app-header-saved"
            type="button"
            onClick={onOpenSaved}
          >
            <Bookmark aria-hidden="true" size={16} />
            <span className="app-header-saved-label">Saved</span>
            {savedCount > 0 ? (
              <span className="app-header-saved-count" aria-hidden="true">
                {savedCount}
              </span>
            ) : null}
          </button>
        ) : null}
        <ThemeToggle />
      </nav>
    </header>
  );
}

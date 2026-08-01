import Image from "next/image";
import Link from "next/link";

import { ThemeToggle } from "@/components/ui/ThemeToggle";

export function AppHeader() {
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
        <ThemeToggle />
      </nav>
    </header>
  );
}

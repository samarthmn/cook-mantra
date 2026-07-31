import type { Metadata } from "next";
import Link from "next/link";

import { AppHeader } from "@/components/layout/AppHeader";
import { DesignGuide } from "@/features/design-guide/DesignGuide";

export const metadata: Metadata = {
  title: "Design guide",
  description: "The reusable visual and interaction language behind Cook Mantra.",
};

export default function DesignGuidePage() {
  return (
    <div className="app-shell">
      <AppHeader />
      <div className="design-guide-back">
        <Link className="btn btn-ghost" href="/">
          ← Back to the app
        </Link>
      </div>
      <DesignGuide />
    </div>
  );
}

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AppHeader } from "@/components/layout/AppHeader";
import { DesignGuide } from "@/features/design-guide/DesignGuide";

export const metadata: Metadata = {
  title: "Design guide",
  description: "The reusable visual and interaction language behind Cook Mantra.",
};

export default function DesignGuidePage() {
  // Internal design reference: browsable in development, absent in production.
  if (process.env.NODE_ENV === "production") notFound();

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

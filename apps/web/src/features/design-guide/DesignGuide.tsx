import {
  Camera,
  Check,
  Clock,
  Flame,
  Image as ImageIcon,
  Moon,
  Plus,
  RefreshCw,
  TriangleAlert,
  Utensils,
  Users,
  X,
  type LucideIcon,
} from "lucide-react";

type Swatch = {
  name: string;
  role: string;
  value: string;
};

type GuideIcon = {
  name: string;
  Icon: LucideIcon;
};

const lightSwatches: Swatch[] = [
  { name: "Ground", role: "--color-bg", value: "#f3f2f2" },
  { name: "Surface", role: "--color-surface", value: "#eae9e9" },
  { name: "Ink", role: "--color-text", value: "#201e1d" },
  { name: "Accent", role: "--color-accent", value: "#ec3013" },
  { name: "Text red", role: "--color-accent-text", value: "#ae1800" },
  { name: "Fill red", role: "--color-accent-fill", value: "#dd2b0f" },
  {
    name: "Divider",
    role: "ink at 52%",
    value: "color-mix(in srgb, #201e1d 52%, transparent)",
  },
];

const darkSwatches: Swatch[] = [
  { name: "Ground", role: "--color-bg", value: "#1a1817" },
  { name: "Surface", role: "--color-surface", value: "#242120" },
  { name: "Ink", role: "--color-text", value: "#f0eeed" },
  { name: "Accent", role: "--color-accent", value: "#ec3013" },
  { name: "Text red", role: "--color-accent-text", value: "#ff9783" },
  { name: "Fill red", role: "--color-accent-fill", value: "#dd2b0f" },
  {
    name: "Divider",
    role: "ink at 52%",
    value: "color-mix(in srgb, #f0eeed 52%, transparent)",
  },
];

const guideIcons: GuideIcon[] = [
  { name: "camera", Icon: Camera },
  { name: "gallery", Icon: ImageIcon },
  { name: "check", Icon: Check },
  { name: "remove", Icon: X },
  { name: "add", Icon: Plus },
  { name: "time", Icon: Clock },
  { name: "calories", Icon: Flame },
  { name: "servings", Icon: Users },
  { name: "more ideas", Icon: RefreshCw },
  { name: "warning", Icon: TriangleAlert },
  { name: "dish", Icon: Utensils },
  { name: "theme", Icon: Moon },
];

const layoutRules = [
  "Start with one content column; let auto-fit grids create additional columns when space allows.",
  "Use a clamped 16–48px page gutter and keep app content within 1160px.",
  "Keep the primary action in the fixed bottom bar on Confirm and Choose screens.",
  "Let the four-step progress row and recipe tabs scroll horizontally on narrow screens.",
  "Keep completed-dish previews in their own bordered block so recipe actions can wrap independently.",
  "Keep every touch target at least 44px; use 52px rows for lists and method steps.",
  "Use 2px section rules, 1px row rules, square corners, and selection-only elevation.",
  "Reserve fluid bottom clearance so a wrapped fixed action bar never covers content.",
  "Test intrinsic reflow at 360px, 768px, and 1280px without page-level horizontal scroll.",
];

const accessibilityRules = [
  "Use native controls first and preserve a visible red focus ring for keyboard users.",
  "Pair icons with visible text or an accessible name; icons never carry meaning alone.",
  "Use aria-pressed for option and method toggles, aria-selected for tabs, and aria-expanded for disclosures.",
  "Announce job progress and failures through status or alert live regions.",
  "Combine color with text, checks, borders, or line-through treatment so state remains clear.",
  "Honor reduced-motion preferences and keep motion limited to progress, disclosure, and theme changes.",
  "Keep fixed actions above the mobile safe area and confirm that wrapped bars do not cover content.",
  "On a screen change, scroll to the top and move focus to the new page heading.",
  "Label every generated preview as AI image and describe the dish in its image alternative text.",
];

function SwatchGroup({ title, swatches }: { title: string; swatches: Swatch[] }) {
  return (
    <div>
      <h3>{title}</h3>
      <div className="swatch-grid">
        {swatches.map((swatch) => (
          <div className="swatch" key={`${title}-${swatch.name}`}>
            <div
              aria-hidden="true"
              className="swatch-chip"
              style={{ background: swatch.value }}
            />
            <div className="swatch-label">
              <div className="card-title">{swatch.name}</div>
              <div className="text-muted">{swatch.role}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function DesignGuide() {
  return (
    <main className="design-guide-main">
      <section aria-labelledby="guide-intro-title">
        <div className="kicker">Design guide</div>
        <h1 className="guide-intro-title" id="guide-intro-title">
          Honest, structural, flush left.
        </h1>
        <p className="screen-intro">
          Cook Mantra uses Archivo, one red accent, square edges, and visible rules to
          organise every screen. Its product rule shapes the visual language:{" "}
          <strong>nothing is assumed until the user confirms it.</strong>
        </p>
      </section>

      <section aria-label="Typography">
        <h2 className="guide-section-title">01 — Typography</h2>
        <p className="text-muted">
          Archivo carries both headings and body copy. Weight 800 creates hierarchy; 600
          marks emphasis; 400 keeps explanations quiet.
        </p>
        <div className="guide-type-list">
          <div className="guide-type-row">
            <h2 className="screen-title">Screen title</h2>
            <span className="text-muted">clamped screen heading · 800</span>
          </div>
          <div className="guide-type-row">
            <h3 className="recipe-title">Recipe name</h3>
            <span className="text-muted">recipe heading · 800</span>
          </div>
          <div className="guide-type-row">
            <p>Body copy explains, quietly. 15px with a 1.55 line height.</p>
            <span className="text-muted">body · 400</span>
          </div>
          <div className="guide-type-row">
            <span className="kicker">Step 2 — Confirm</span>
            <span className="text-muted">kicker · uppercase · accent</span>
          </div>
        </div>
      </section>

      <section aria-label="Color, light and dark">
        <h2 className="guide-section-title">02 — Color, light and dark</h2>
        <p className="text-muted">
          The interface stays mostly ink on ground. Red marks the current step, the main
          action, selection, missing ingredients, and warnings. Dark mode swaps role
          values; components do not change.
        </p>
        <SwatchGroup title="Light theme" swatches={lightSwatches} />
        <SwatchGroup title="Dark theme" swatches={darkSwatches} />
      </section>

      <section aria-label="Icons">
        <h2 className="guide-section-title">03 — Icons</h2>
        <p className="text-muted">
          Use Lucide on currentColor with a 2px interface stroke. Pair every icon with
          text or an accessible name.
        </p>
        <div className="icon-grid">
          {guideIcons.map(({ name, Icon }) => (
            <div className="icon-tile" key={name}>
              <Icon aria-hidden="true" size={22} strokeWidth={2} />
              <span className="text-muted">{name}</span>
            </div>
          ))}
        </div>
      </section>

      <section aria-label="Reusable product patterns">
        <h2 className="guide-section-title">04 — Product patterns</h2>
        <div className="pattern-grid">
          <article>
            <h3 className="card-title">Buttons and actions</h3>
            <p className="text-muted">
              Use one primary action per screen. Keep every wide label flush left.
            </p>
            <div className="upload-zone-actions">
              <button className="btn btn-primary btn-lg" type="button">
                Generate recipe ideas
              </button>
              <button className="btn btn-secondary btn-lg" type="button">
                More ideas
              </button>
              <button className="btn btn-ghost" type="button">
                Back
              </button>
            </div>
          </article>

          <article>
            <h3 className="card-title">Ingredient sources</h3>
            <p className="text-muted">
              Source and confidence stay visible from confirmation through the final
              recipe.
            </p>
            <div className="ingredient-row-tags">
              <span className="tag tag-neutral">92% · detected</span>
              <span className="tag tag-neutral">pantry</span>
              <span className="tag tag-accent">added by you</span>
              <span className="tag tag-outline">check this</span>
            </div>
          </article>

          <article>
            <h3 className="card-title">Form fields</h3>
            <p className="text-muted">
              Labels stay visible and controls use the surface and divider roles.
            </p>
            <div className="field">
              <label htmlFor="guide-allergens">Allergens to avoid</label>
              <input
                className="input"
                id="guide-allergens"
                placeholder="e.g. peanuts, shellfish"
                type="text"
              />
            </div>
          </article>

          <article>
            <h3 className="card-title">Honest recovery</h3>
            <p className="text-muted">
              Warnings explain what happened and what the user can do next.
            </p>
            <div className="notice notice-accent" role="status">
              <TriangleAlert
                aria-hidden="true"
                className="notice-icon"
                strokeWidth={2}
              />
              <div>
                <strong>Detection was weak on this photo.</strong> Add anything we
                missed by hand, or retake the photo.
              </div>
            </div>
          </article>

          <article className="option-card">
            <div className="option-card-masthead" aria-hidden="true">
              <Utensils className="option-card-masthead-icon" />
              <span className="option-card-index">
                01<small>batch 01</small>
              </span>
            </div>
            <div className="option-card-content">
              <div className="option-card-heading">
                <span className="option-card-cuisine">North Indian</span>
              </div>
              <h3 className="option-card-title">Palak Paneer</h3>
              <p className="option-card-summary">
                Silky spinach gravy with soft paneer cubes — a beginner-friendly
                classic.
              </p>
              <div className="option-card-meta">
                <span className="meta-with-icon">
                  <Clock aria-hidden="true" size={13} strokeWidth={2} />
                  35 min
                </span>
                <span>Easy</span>
              </div>
              <div className="option-card-tags">
                <span className="tag tag-neutral">uses 8 of 11</span>
                <span className="tag tag-neutral">Vegetarian</span>
              </div>
              <div className="option-card-honesty option-card-missing">
                <strong>Missing:</strong> cream
              </div>
            </div>
          </article>
        </div>
      </section>

      <section aria-label="Layout and responsiveness">
        <h2 className="guide-section-title">05 — Layout &amp; responsiveness</h2>
        <div className="guide-rule-list" role="list">
          {layoutRules.map((rule, index) => (
            <div className="guide-rule-row" key={rule} role="listitem">
              <span className="guide-rule-index">5.{index + 1}</span>
              <span>{rule}</span>
            </div>
          ))}
        </div>
      </section>

      <section aria-label="Accessibility and interaction">
        <h2 className="guide-section-title">06 — Accessibility &amp; interaction</h2>
        <div className="guide-rule-list" role="list">
          {accessibilityRules.map((rule, index) => (
            <div className="guide-rule-row" key={rule} role="listitem">
              <span className="guide-rule-index">6.{index + 1}</span>
              <span>{rule}</span>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}

# Handoff: Cook Mantra — full UI/UX workflow

> **Superseded historical handoff.** Do not implement the product behavior in
> this prototype README. The current sources of truth are
> [`docs/prd.md`](../docs/prd.md),
> [`docs/architecture-doc.md`](../docs/architecture-doc.md), and
> [`docs/ui-style-guide.md`](../docs/ui-style-guide.md).

## Overview

Cook Mantra turns a photo of ingredients into complete recipes. The user photographs what they have, an Ingredient Extraction Agent lists what it sees, the user **reviews and confirms** the list (detected + pantry staples + manually added), a Master Chef Agent proposes dish ideas enriched with nutrition (Nutrition Agent) and AI dish illustrations (Image Agent), the user selects one or more dishes, and one Specialized Recipe Agent per dish writes the complete recipe **in parallel**.

This bundle contains the complete, working design for that flow: a 4-step wizard web app (mobile-first, responsive to tablet and desktop) with light and dark modes, plus a design guide page.

**Core product rule (do not break):** nothing is ever treated as available unless the user confirmed it. Every ingredient carries a visible source (`detected` / `pantry` / `added by you`), missing ingredients are stated plainly, dish images are labelled "AI illustration", and nutrition is labelled an estimate.

## About the Design Files

The files in this bundle are **design references created in HTML** — working prototypes that show the intended look and behavior. They are NOT production code to copy directly. Your task is to **recreate these designs in the target codebase's environment** (React, Vue, Svelte, native, etc.) using its established patterns — or, if no frontend exists yet (the Cook Mantra repo is currently backend-only: FastAPI + LangGraph + Ollama), choose an appropriate framework and implement the designs there.

The prototypes simulate the backend with timers and mock data. In production, wire each waiting screen to the real API: `POST /api/v1/sessions` (upload + extraction job), job polling (`queued → running → succeeded/failed`), ingredient confirmation, option generation, and parallel complete-recipe generation. Session stages in the API map 1:1 to the wizard steps.

## Fidelity

**High-fidelity.** Colors, typography, spacing, states, and copy are final. Recreate pixel-perfectly. The visual language is the **Modernist design system** (included at `_ds/…/styles.css` — the single source of truth for tokens and component classes). Key traits: Archivo only, one red accent, **zero border radius anywhere**, strong 2px rules organizing sections, everything flush left (including button labels), no decorative gradients or shadows.

## How to view the prototypes

Open `Cook Mantra.dc.html` in a browser from this folder (keep `support.js` and `_ds/` beside it; internet needed for fonts/runtime). Or read the files as source: each `.dc.html` contains the full markup (inside `<x-dc>`) and all behavior (the `Component` class in the script tag).

## Screens / Views

The app is one page with a persistent shell and 4 wizard steps. Shell:

- **Header** (`.nav`, 2px bottom rule, padding `12px clamp(16px,4vw,48px)`): brand = 14×14px solid red square + "COOK MANTRA" (Archivo 800, 18px), pushed left; right side: "Design guide" text link (13px, underlined by 1px divider border) and a 44×44px icon button toggling light/dark (sun icon shown in dark mode, moon in light).
- **Stepper** (below header, 2px bottom rule, horizontal scroll on narrow screens, gap `clamp(16px,3vw,40px)`): 4 items — 1 Photo, 2 Confirm, 3 Choose, 4 Cook. Item = 22×22px numbered box (1.5px border) + uppercase Archivo-800 12px label, letter-spacing .08em, 14px vertical padding. Current step: red number box (white number), red label, inset 3px red underline. Completed steps: ink, clickable (navigates back). Future steps: 40% ink, disabled. During a job the stepper highlights the job's target step and all items lock.
- **Main column**: max-width 1160px, centered, gutter `clamp(16px,4vw,48px)`, bottom padding 120px (clears the sticky bar).
- **Sticky action bar** (confirm + options steps only): fixed bottom, full-width, 2px top rule, bg = page ground, inner column matches main. Left: bold status line (Archivo 800 15px) + muted 12px hint. Right: actions incl. the single primary button (48px min-height, red fill). On mobile it wraps.

### 1. Upload ("What's in your kitchen?")

- Red uppercase kicker "STEP 1 — INGREDIENTS" (11px, ls .12em, 800) → h1 `clamp(32px,6vw,52px)` → muted intro (15px, max 52ch) → 2px rule.
- **Drop zone**: max-width 720px, `2px dashed` divider border, padding `clamp(32px,6vw,64px) 16px`, flush-left column, gap 16px. Contents: 40px red camera icon (stroke 1.5), title "Photograph your ingredients" (Archivo 800, 19px), muted caption "Lay them out on a counter — one photo is enough. Photos stay on this device.", then two buttons: primary "Take a photo" (camera icon) + secondary "Choose from gallery" (image icon), both 48px min-height. 11px muted note beneath.
- Below: ghost buttons "Type ingredients instead →" (accent) and "Simulate weak detection" (ink at 55% — prototype-only affordance; in production this is the natural weak-detection path).
- In production: camera capture / file picker; image never leaves the device except to the local API.

### 2. Job / waiting screens (shared pattern, max-width 640px)

Used three times (extraction, idea generation, recipe generation). Kicker + h1 `clamp(28px,5vw,42px)` + muted sub + 2px rule, then a **checklist**: one row per pipeline stage (min-height 56px, 1px bottom rule, 12px gap). Row = status glyph + label (14px, 600; pending rows at 45% opacity) + muted 12px detail + optional `agent` neutral tag for AI agents.

- Status glyphs (22×22): pending = 2px divider-border empty square; running = 2.5px border ring, top border red, `border-radius:50%`, spinning 0.8s linear (the ONE permitted circle in the UI); done = solid red square with white 3px check.
- Sequential jobs run rows one-by-one; the **recipe job runs all agent rows simultaneously** (one "Specialized Recipe Agent — {dish}" row per selected dish + final "Collecting results" row) — parallelism must be visible.
- Below: ghost "← Cancel" (returns to previous step) and "Simulate a failure" (prototype-only).
- Checklist copy — extraction: "Photo uploaded / Stays on this device", "Ingredient Extraction Agent / Identifying items in the image" (agent), "Scoring confidence / Uncertain items get flagged for you". Ideas: "Master Chef Agent / Drafting dishes from your confirmed list", "Nutrition Agent / Estimating calories, macros and diet tags", "Image Agent / Illustrating each dish (AI illustration)" (all agent), "Assembling options / Combining ideas, nutrition and images".

### 3. Confirm ("Check what we found")

Kicker "STEP 2 — CONFIRM", h1, sub "Rename, remove, or add anything. Only ingredients you confirm here are used in recipes.", 2px rule. Two panels in `grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr))`, gap `clamp(24px,3vw,40px)` — side-by-side on desktop, stacked on mobile.

**Panel A — "DETECTED IN YOUR PHOTO"** (h6 label, 70% ink):

- One row per detected item (min-height 52px, 1px bottom rule): editable name input (borderless/transparent, 600 weight, renaming preserves source), tags, 44×44 secondary icon button with ✕ to remove.
- Confidence tag: neutral tag "{conf}%" . Items **below 70% confidence** additionally get an outline tag "check this" (red border/text) BEFORE the % tag.
- User-added items render in the same list style with a red-tinted tag "added by you" instead of a % tag.
- Empty state (weak/manual entry): muted row "Nothing detected — add ingredients below."
- Add row: `.input` "Add an ingredient…" + secondary button "+ Add" (44px), submits on Enter.
- Weak-detection banner (when applicable, above the panels): 3px red left border on surface bg — "**Detection was weak on this photo.** We only found what's listed below — add the rest by hand, or retake the photo." (retake = inline ghost link).

**Panel B — "PANTRY STAPLES — TICK WHAT YOU ACTUALLY HAVE"**:

- Muted 12px note: "These are common in most kitchens but weren't in your photo. Nothing is assumed until you tick it."
- 7 checkbox rows (52px, 1px rules): Salt, Pepper powder, Oil or ghee, Chilli powder, Onion, Garlic, Ginger. Checkbox = 22×22 square, 2px divider border; checked = red fill + white check. Unticked labels at 60% opacity, ticked at 100%. Neutral tag "pantry" at right. Whole row is the click target. **All default to unticked.**
- **Preferences panel** below (1px border box, collapsed by default): header row (48px) "Preferences — optional" (Archivo 800 14px) + muted live summary ("Veg · serves 2 · ≤ 45 min · 4 ideas") + chevron (rotates 180°, .2s). Expanded body (1px top rule, auto-fit grid minmax 220px): Diet segmented control (None/Veg/Vegan), Servings (1/2/4), Max cooking time (30/45/60 min), **Ideas per batch (3/4/6)** — segmented controls are `.seg`/`.seg-opt`, selected = red fill + ground text, 40px min-height — and "Allergens to avoid" text input (placeholder "e.g. peanuts, shellfish").

**Sticky bar**: "{N} ingredients confirmed" + hint "Detected + pantry + added by you" (or "Add at least one ingredient to continue" when 0); primary "Generate recipe ideas →", **disabled at 0 ingredients** (45% opacity).

### 4. Options ("{N} ideas from your {M} ingredients")

Kicker "STEP 3 — CHOOSE", h1 with live counts, sub: "Pick one or more. Every image is an AI illustration — your dish may look different. Nutrition is an estimate, not medical advice." 2px rule.

**Card grid**: `repeat(auto-fill,minmax(min(100%,280px),1fr))`, gap `clamp(16px,2.5vw,28px)`. Card (surface bg, `1px` divider outline, no radius, whole card toggles selection; role=button, Enter/Space toggles):

- **Image area**: 16:9, neutral-200 bg, centered utensils icon placeholder (production: the generated dish image, treat via `.grayscale`), bottom-left neutral tag "AI ILLUSTRATION" (9px uppercase). Selected: 28×28 red square with white check, top-right.
- Body (12px padding, 8px gaps): red uppercase kicker = cuisine (+ red-tinted tag "new batch" on later batches); title 19px Archivo 800; summary 13px at 80%; meta row (11px, 50% ink): clock icon + "{t} min", difficulty.
- **Nutrition strip**: 4 equal columns over a 1px top rule — value (Archivo 800 14px) over 9px uppercase muted label: kcal / protein / carbs / fat (grams).
- Tag row: neutral "uses {x} of {M}" (x clamped ≤ confirmed count) + diet tags (Vegetarian, Vegan, Gluten-free…).
- Honesty rows (only when applicable): "**Missing:** cream" in accent-700 over a 1px rule; muted "Optional: coriander"; accent-700 warning-triangle row "Contains dairy and soy".
- **Selected state**: outline becomes `2px solid` red + `--shadow-md` elevation.
- Ghost "← Edit ingredients" below the grid.

**Sticky bar**: "{n} of {N} selected" + hint ("Pick at least one dish" / "Each recipe is written by its own agent, in parallel"); secondary "↻ More ideas"; primary "Create {n} recipe(s) →" (disabled at 0).

- **More ideas**: runs the ideas job again ("A fresh batch" / "Recipes you have already seen are remembered and excluded."), appends {ideas-per-batch} new cards tagged "new batch"; previously shown recipes are session-excluded. When the session pool is exhausted, show inline notice (3px divider left border): "No fresh ideas left for these ingredients — recipes already shown are never repeated. Try editing your ingredient list."

### 5. Recipes ("Your {n} recipes, ready")

Kicker "STEP 4 — COOK", h1. If >1 recipe: **tab strip** (1px border box, fits content, horizontal scroll on mobile): one button per recipe (Archivo 800 13px, 12×18px padding, 44px min-height, 1px right rules); active tab = red fill + ground text. 2px rule below.

Per recipe:

- Title row (wraps): h2 `clamp(24px,4vw,34px)` + red-tinted cuisine tag + muted meta: clock "{t} min", users "serves {s}".
- Muted 13px assumptions line: "Assumes: medium spice level; fresh spinach, not frozen".
- **Two columns** (flex-wrap: ingredients `flex:1 1 280px; max-width:420px`, method `flex:2 1 340px`; stacks on mobile):
  - **INGREDIENTS**: rows (44px, 1px rules): quantity (Archivo 800 13px, 64px min-width), name (14px), status tag — `confirmed` (neutral), `pantry` (red tint), `optional` (accent-2 tint), `missing` (red outline). Below: 1px-border box "PER SERVING — ESTIMATE": 4-column strip (18px Archivo-800 values, 10px uppercase labels) kcal/protein/carbs/fat + accent-700 "Contains dairy." + 11px muted "Estimates only — not medical advice."
  - **METHOD — TAP A STEP WHEN DONE** + red-tinted progress tag "{d} / {n} done". Steps = full-width buttons (min-height 52px, 1px rules, 12px padding): 28×28 number square (2px divider border, Archivo 800 13px) + step text (15px/1.5). Done: square fills red with white check; text 45% opacity + line-through. State is per-recipe.
  - Below method, two auto-fit columns: "TIPS" (2px red left border, 13px paragraphs) and "SUBSTITUTIONS" (2px divider left border) — substitutions reference confirmation state, e.g. "Cream → whisked curd (you confirmed curd)."
- Footer: secondary "← Back to recipe ideas" (returns to options, selection intact — no re-upload) + ghost "Start over with a new photo" (full reset).

### 6. Error / edge states

- **Job failure**: return to the step that preceded the job (state untouched — matches API semantics) and show an alert banner above the content: 2px red border, accent-100 bg, warning triangle, title "That took too long" (Archivo 800 14px, accent-800), body "The model timed out. Nothing was lost — your ingredients are exactly as you left them." (13px accent-800), primary "Try again" (re-runs the job) + ghost "Dismiss".
- **Weak detection** → banner on Confirm (see above). **Manual entry** → Confirm with empty detected list. **0 ingredients / 0 selected** → disabled primary + hint. **Ideas exhausted** → inline notice.

## Interactions & Behavior

- Wizard order: Upload → (extract job) → Confirm → (ideas job) → Options → (recipes job) → Recipes. Stepper allows backward navigation to any previously reached step; forward is locked. Cancel during a job returns to the step before it.
- Card select/deselect: click or Enter/Space; multi-select allowed; must select ≥1 to proceed.
- Step check-off in Method is a toggle; per-recipe progress tag updates live.
- Theme: defaults to `prefers-color-scheme`, manual toggle overrides and persists (localStorage `cm-theme`); implemented by setting `data-theme="dark"` on `<html>` (see Design Tokens). 0.25s bg/color transition.
- Motion is functional only: 0.8s linear spinner, 0.2s chevron rotation, 0.25s theme cross-fade. Nothing else animates.
- Hover/active/focus states come from the design system stylesheet (accent-ramp tints, 2px red `:focus-visible` outline). Do not use default blue focus rings.
- Touch targets ≥44px throughout (rows 52px, buttons 44–48px).

## State Management

- `step` (upload | job | confirm | options | recipes) + `maxReached` (stepper unlock level) + current `jobKind`.
- `detected[]`, `added[]` (each `{id, name, source, confidence?}` — renames preserve source), `pantry[]` (`{name, on}`), `preferences` (diet, servings, maxTime, ideasPerBatch, allergens).
- `confirmedCount` = detected + added + ticked pantry (derived).
- `options[]` with session-level exclusion of previously shown; `selected` set; batch markers.
- `recipes[]` keyed by option id; `activeTab`; `checkedSteps` per recipe.
- `error` (title, message, retry closure); `theme`.
- Production: session id + job polling replace the timers; a failed job restores the prior session stage (the API already guarantees this).

## Design Tokens

Source of truth: `_ds/modernist-3697d321-e48c-4021-b952-12f9a879c0e6/styles.css`. Never hard-code values the tokens carry.

**Light (root):** bg `#f3f2f2` · surface `#eae9e9` · text `#201e1d` · accent `#ec3013` · divider = text @40% · neutral ramp 100–900 `#f8f4f4 → #2d2b2b` · accent ramp 100–900 `#fff2ef → #4d170e` (600 `#dd2b0f`, 700 `#ae1800` for accent body text) · shadows sm/md/lg ink-tinted.

**Dark (`html[data-theme="dark"]` overrides — same variables, components unchanged):**
bg `#1a1817` · surface `#242120` · text `#f0eeed` · divider = text @35% · neutral-100/200/300 `#2d2b2b / #3a3737 / #575353`, neutral-700/800/900 `#bab6b6 / #d7d3d3 / #f8f4f4` · accent-100/200/300 `#4d170e / #6f1707 / #9c1a02`, accent-600/700/800/900 `#ff563c / #ff9783 / #ffc4b8 / #fff2ef` · accent-2-100/200/700/800 `#471d16 / #67251c / #ff9784 / #ffc4b9` · shadows: black at 50–65%. Accent base `#ec3013` is unchanged.

**Type:** Archivo only (Google Fonts, 400/600/800). Body 15px/1.55. h1 42 (clamped responsive), h2 32, h3 25, h4 20, h6 13 uppercase ls .08em. Kicker: 11px, 800, uppercase, ls .12em, accent. Headings ls -0.015em.

**Spacing:** 4 / 8 / 12 / 16 / 24 / 32px scale. Page gutter `clamp(16px,4vw,48px)`. **Radius: 0px everywhere** (sole exception: the spinner ring). Rules: 2px between major sections, 1px between rows.

**Responsiveness:** no breakpoint CSS — intrinsic layout only: auto-fit/auto-fill grids (`minmax(min(100%,340px),1fr)` panels, 280px cards), flex-wrap, `clamp()` type and gutters. Verified at 360px, 768px, 1280px.

## Assets

- **Icons:** Lucide (lucide.dev), inline SVG on `currentColor`, stroke 2 (1.5 above 32px), never meaning-bearing without a text label. Set used: camera, image, plus, x, check, clock, flame, users, refresh-cw, alert-triangle, utensils, sun, moon, chevron-down, arrow-left/right.
- **Dish images:** AI-generated per option by the Image Agent (backend `x/z-image-turbo`); always labelled "AI illustration"; prototype uses a flat placeholder. Photography prints grayscale (`.grayscale` wrapper).
- No other imagery, no emoji.

## Files

- `Cook Mantra.dc.html` — the full prototype: all markup, all behavior, all mock data (recipe/option copy worth reusing).
- `Design Guide.dc.html` — the design-guide page: type, color (light/dark), icons, product patterns, layout rules.
- `support.js` — prototype runtime only (do not port).
- `_ds/modernist-3697d321-e48c-4021-b952-12f9a879c0e6/styles.css` — the design-system stylesheet: tokens + component classes (`.btn*`, `.tag*`, `.card*`, `.input`, `.seg*`, `.nav`, `.table`, `.hr`, `.grayscale`). Port these as your base components.
- `_ds/…/readme.md` — the Modernist design-system guide (do/don't rules).
- `docs/prd.md` is in the product repo and remains the functional source of truth.

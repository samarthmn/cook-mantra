# Cook Mantra UI style guide

Cook Mantra uses a flat Modernist system: Archivo type, one red accent, square
edges, and visible rules. The interface should feel direct and dependable.
Structure carries the design; decoration does not.

The CSS source of truth is
[`apps/web/src/app/globals.css`](../apps/web/src/app/globals.css). Use its tokens
and classes before adding a new value or pattern. Update this guide when a
shared token or component contract changes.

## Product principles

The interface must preserve these rules on every screen:

1. Nothing counts as available until the user confirms it.
2. Every ingredient shows its source: detected, pantry, or added by you.
3. Missing and optional ingredients remain visible.
4. Generated dish art always says **AI illustration**.
5. Nutrition always says **estimate** and never presents medical advice.
6. Parallel recipe work stays visible as one agent row per selected dish.
7. Local previews and simulated states are labelled before they can be mistaken
   for live API output.

These rules apply to new components as well as the four-step recipe flow.

## Visual direction

- Set all interface text in Archivo.
- Keep headings, content, controls, and wide button labels flush left.
- Use red for the current step, primary action, selection, and honesty markers.
- Use a 2px rule between major sections and a 1px rule between rows.
- Keep every corner square. The running-status spinner is the sole rounded
  product container.
- Use shadows only for selected cards and true overlay elevation.
- Render content photography in grayscale. Never tint it.
- Use Lucide icons with `currentColor`; use a 2px stroke below 32px and a 1.5px
  stroke at larger display sizes.
- Avoid gradients, decorative blur, ornamental motion, centered hero copy, and
  unstructured floating cards.

## Tokens

### Color roles

Use role tokens instead of raw color values.

| Token                 | Light role          | Use                                                   |
| --------------------- | ------------------- | ----------------------------------------------------- |
| `--color-bg`          | Ground              | Page and sticky-bar background                        |
| `--color-surface`     | Surface             | Cards, inputs, notices, and panels                    |
| `--color-text`        | Ink                 | Headings and normal body copy                         |
| `--color-text-muted`  | Muted ink           | Supporting metadata and disclaimers                   |
| `--color-text-subtle` | Subtle ink          | Field labels and secondary headings                   |
| `--color-accent`      | Brand red           | Brand marks, large shapes, and decorative rules       |
| `--color-accent-text` | Accessible red      | Kicker, link, cuisine, and small icon foregrounds     |
| `--color-accent-fill` | Accessible red fill | Primary and selected-control backgrounds              |
| `--color-on-accent`   | On-red foreground   | Text and checks on `--color-accent-fill`              |
| `--color-accent-700`  | Deep red            | Small warning or missing-ingredient text              |
| `--color-divider`     | Translucent ink     | Structural and row rules                              |
| `--color-neutral-*`   | Neutral ramp        | Neutral labels, placeholders, and media beds          |
| `--color-accent-*`    | Red ramp            | Tints, hover states, pressed states, and warning text |
| `--color-accent-2-*`  | Secondary red ramp  | Optional-status tint; do not introduce a second hue   |

Light primitives are:

- Ground: `#f3f2f2`
- Surface: `#eae9e9`
- Ink: `#201e1d`
- Accent: `#ec3013`
- Divider: ink at 52%

Dark mode changes the same roles rather than changing components:

- Ground: `#1a1817`
- Surface: `#242120`
- Ink: `#f0eeed`
- Divider: ink at 52%
- Accent: `#ec3013`

Set `data-theme="dark"` on the root `html` element. The root also sets the
browser `color-scheme`, so native controls match the selected theme. Keep the
theme choice shared across routes and load it before paint to avoid a light-mode
flash.

Use accent ramp steps 100–300 for tinted fills. Use the semantic text, fill,
hover, active, and on-accent roles for interactive states so normal-size text
keeps at least 4.5:1 contrast in both themes. The base accent remains available
for the exact brand red on large shapes and decorative rules. Use
`--color-accent-700` for paragraph-size warnings.

### Typography

The root layout should load Archivo with weights 400, 600, and 800 through
`next/font` and expose it as `--font-archivo`. Global styles map that variable to
`--font-heading` and `--font-body`.

| Role                  | Size and weight                 | Class or token           |
| --------------------- | ------------------------------- | ------------------------ |
| Upload title          | `clamp(32px, 6vw, 52px)`, 800   | `.screen-title-upload`   |
| Standard screen title | `clamp(30px, 5.5vw, 48px)`, 800 | `.screen-title`          |
| Job title             | `clamp(28px, 5vw, 42px)`, 800   | `.screen-title-job`      |
| Recipe title          | `clamp(24px, 4vw, 34px)`, 800   | `.recipe-title`          |
| Body                  | 15px/1.55, 400                  | `--font-size-body`       |
| Emphasis              | 600                             | `--font-emphasis-weight` |
| Button                | 14px, 800                       | `.btn`                   |
| Kicker                | 11px, 800, uppercase, `.12em`   | `.kicker`                |
| Section label         | 13px, 800, uppercase, `.08em`   | `.panel-heading`         |

Headings use a 1.12 line height and `-.015em` tracking. Keep line lengths near
52–60 characters for explanatory copy.

### Spacing, sizing, and elevation

The spacing scale is 4, 8, 12, 16, 24, and 32px:

```css
var(--space-1)
var(--space-2)
var(--space-3)
var(--space-4)
var(--space-6)
var(--space-8)
```

Use the semantic sizing tokens for repeated interaction geometry:

| Token                 | Role                                |
| --------------------- | ----------------------------------- |
| `--control-height`    | 44px minimum touch target           |
| `--control-height-lg` | 48px primary action                 |
| `--row-height`        | 52px list or method row             |
| `--job-row-height`    | 56px agent-status row               |
| `--status-size`       | 22px checkbox or job status         |
| `--step-marker-size`  | 28px selected mark or method number |
| `--page-gutter`       | `clamp(16px, 4vw, 48px)`            |
| `--page-max-width`    | 1160px app column                   |
| `--guide-max-width`   | 1080px guide column                 |
| `--sticky-clearance`  | Fluid space reserved above actions  |

All radius tokens resolve to zero. `--shadow-md` marks a selected option card;
`--shadow-lg` belongs to a dialog or top-level overlay. Do not add elevation to
ordinary panels.

## Layout and responsiveness

Build mobile-first layouts with intrinsic grids and flex wrapping:

- `.page-main` centers the 1160px content column and applies the page gutter.
- `.confirm-grid` uses auto-fit columns with a 340px preferred minimum.
- `.preferences-grid` uses auto-fit columns with a 220px preferred minimum.
- `.option-grid` uses auto-fill cards with a 280px preferred minimum.
- `.recipe-layout` wraps a 420px ingredient rail beside the flexible method.
- `.recipe-notes-grid` uses auto-fit 240px notes.
- `.progress-stepper` and `.recipe-tabs` scroll horizontally when needed.
- `.sticky-action-inner` wraps actions and includes the bottom safe-area inset.

Prefer `minmax(min(100%, <preferred width>), 1fr)`, flex wrapping, and `clamp()`
over page-specific breakpoints. Test at 360px, 768px, and 1280px. Long recipe
names, translated labels, and a two-line sticky summary must not obscure an
action or create horizontal page scroll.

## Core primitives

### Rules and text

- `.hr` or `.section-rule`: strong 2px divider.
- `.text-muted`: supporting text.
- `.text-subtle`: stronger secondary text.
- `.kicker`: red screen-step label.
- `.visually-hidden`: accessible-only content.
- `.grayscale` or `.grayscale-media`: image treatment.

### Buttons

Start every action with `.btn`:

- `.btn-primary`: the single main action on a screen.
- `.btn-secondary`: bordered supporting action.
- `.btn-ghost`: low-priority link-like action.
- `.btn-icon`: 44px square icon control; always supply an accessible name.
- `.btn-lg`: 48px high primary or prominent secondary action.
- `.btn-block`: full-width, still left aligned.

Button labels remain left aligned. Icon-only buttons are the sole centered
button treatment. A disabled action must use the native `disabled` attribute;
`aria-disabled` alone does not block activation.

### Tags

Combine `.tag` with one role:

- `.tag-neutral`: confidence, detected, pantry, diet, or agent.
- `.tag-accent`: added by you, cuisine, progress, or new batch.
- `.tag-accent-2`: optional ingredient.
- `.tag-outline`: check this or missing ingredient.

Tags support nearby content; they do not replace a field label, error message,
or accessible state.

### Forms

- Wrap a control in `.field` and use a visible label.
- Use `.input` for text fields and text areas.
- Build segmented choices with `.seg`, `.seg-opt`, and native radio inputs.
- Keep form errors adjacent to their controls and use text in addition to red.

Do not hide a native input unless its custom visual state also receives a
visible focus ring.

## Product patterns

### Header and progress

Use `.app-shell`, `.app-header`, `.app-brand`, `.app-brand-mark`, and
`.app-header-actions` for the persistent shell. The text link uses
`.app-header-link`; theme toggle uses `.btn.btn-secondary.btn-icon`.

Build progress with `.progress-stepper`, `.progress-step`, and
`.progress-step-number`:

- `.is-current`: red marker, label, and 3px inset underline.
- `.is-complete`: ink and available for navigation to any previously reached
  step, including returning forward after an edit-free review.
- `.is-locked` or `disabled`: 40% ink and unavailable.

Lock every progress control while an agent job runs. Highlight the job's target
step.

### Screen heading and sticky actions

Group each kicker, title, and introduction in `.screen-header`. Use the title
class that matches the screen type, followed by `.section-rule`. Every screen
heading accepts programmatic focus with `tabindex="-1"`; after a screen change,
scroll to the top and move focus to that heading.

The Confirm and Choose screens use `.sticky-action-bar` with:

- `.sticky-action-inner`
- `.sticky-action-summary`
- `.sticky-action-count`
- `.sticky-action-hint`
- `.sticky-action-actions`

Keep one primary action in this bar. Its disabled state and hint must explain
what the user needs to do next.

### Upload area

Use `.upload-zone` for the dashed 720px photo area. Compose it with
`.upload-zone-icon`, `.upload-zone-title`, `.upload-zone-copy`,
`.upload-zone-actions`, and `.upload-zone-note`.

Camera and gallery inputs need visible labels, file-type validation, and clear
permission or read errors. Do not claim an image stays on-device if the current
deployment sends it to a remote service.

### Agent checklist

Use `.job-shell`, `.job-list`, and `.job-row`. Set `data-status` to `pending`,
`running`, or `done`; combine `.job-status` with the matching status class.

- Pending: empty square and 45% label opacity.
- Running: red-topped spinner ring.
- Done: red square with white check.

Use `.job-label`, `.job-detail`, and the neutral `agent` tag for each row. Put
the checklist in a polite live region and expose a textual status; motion alone
must never communicate progress. Start all Specialized Recipe Agent rows in
parallel, then run the collection row.

Only show Cancel when the underlying operation can actually be cancelled. If a
server job has no cancellation endpoint, replace interruption controls with a
quiet status explaining that the page must stay open. Aborting browser polling
must never be presented as cancelling server work.

### Alerts and notices

- `.alert`: blocking or retryable job failure with a 2px red border.
- `.notice.notice-accent`: weak detection or another important inline warning.
- `.notice.notice-plain`: exhausted ideas or quiet informational state.

Use `role="alert"` for an error that needs immediate announcement. Use
`role="status"` for non-blocking updates. Include a title, a concrete recovery
action, and a dismissal only when the message can safely disappear.

### Ingredient confirmation

Use `.confirm-grid` for the detected and pantry columns.

Detected and added ingredients use `.ingredient-list`, `.ingredient-row`,
`.ingredient-name-input`, `.ingredient-row-tags`, and `.ingredient-add-form`.
Include the ingredient name in each remove button's accessible name. Preserve
the ingredient source when the user renames it.

Pantry rows depend on this sibling order for checked and focus styling:

```html
<label class="pantry-row">
  <input class="pantry-input" type="checkbox" />
  <span class="pantry-checkbox" aria-hidden="true">…</span>
  <span class="pantry-label">Salt</span>
  <span class="tag tag-neutral">pantry</span>
</label>
```

All pantry entries start unchecked. The whole row remains the click target.

Preferences use `.preferences`, `.preferences-trigger`,
`.preferences-summary`, `.preferences-chevron`, `.preferences-panel`, and
`.preferences-grid`. The trigger must set `aria-expanded` and `aria-controls`.
The chevron rotates from that semantic state.

### Recipe option cards

Use a native button with `.option-card` whenever possible. Set `aria-pressed`
for multi-selection. Compose each card with:

- `.option-card-media` and a grayscale `.option-card-image`
- `.option-card-ai-label`
- `.option-card-selected-mark`
- `.option-card-content`
- `.option-card-heading`, `.option-card-cuisine`, and `.option-card-title`
- `.option-card-summary` and `.option-card-meta`
- `.nutrition-strip`, `.nutrition-value`, and `.nutrition-label`
- `.option-card-tags`
- `.option-card-missing`, `.option-card-optional`, and
  `.option-card-allergens`
- `.option-card-warnings` for backend or model caveats

The selected state uses a 2px accent outline, red check, and `--shadow-md`.
Missing, optional, and allergen rows stay visible even when they make an option
less appealing.

### Complete recipe

For multiple recipes, use `.recipe-tabs` as a tablist and `.recipe-tab` for each
tab. Each tab needs `role="tab"`, `aria-selected`, `aria-controls`, and keyboard
arrow navigation. Associate the visible recipe with a tabpanel.

Use `.recipe-title-row`, `.recipe-meta`, and `.recipe-assumptions` above
`.recipe-layout`. The ingredient rail uses `.recipe-ingredient-list`,
`.recipe-ingredient-row`, `.recipe-ingredient-quantity`, and
`.recipe-ingredient-name`. Keep the estimate and disclaimer together in
`.recipe-nutrition`.

Method rows use `.method-list`, `.method-step`, `.method-step-number`, and
`.method-step-text`. Set `aria-pressed` on every step. Completed steps receive a
red check plus struck-through text so the state does not depend on color.

Tips and substitutions use `.recipe-notes-grid` and `.recipe-note`; add
`.recipe-note-accent` to Tips. Keep confirmation-aware wording such as “you
confirmed curd” only when the current ingredient state proves that claim.

## Interaction and accessibility

- Keep touch targets at least 44px. Primary controls should reach 48px.
- Use native buttons, inputs, tabs, and disclosures before recreating semantics.
- Every interactive control needs a visible `:focus-visible` outline.
- Never remove focus without replacing it on the visible control.
- After a workflow screen is replaced, move focus to its `h1`; do not steal
  focus again for progress updates within the same screen.
- Pair icons with visible text or an accessible name.
- Use `aria-pressed` for toggle cards and method steps, `aria-selected` for tabs,
  and `aria-expanded` for disclosures.
- Announce changing jobs and errors with the appropriate live-region role.
- Invalidate generated options and recipes whenever ingredients, diet,
  allergens, servings, time, or option count changes.
- Gate API actions to their accepted backend stage. When a user edits a
  post-confirmation photo session, restart from the retained photo and reconcile
  reviewed names onto the fresh server ingredient IDs.
- Preserve logical heading order; use classes for visual size instead of jumping
  heading levels.
- Do not rely on red, opacity, an icon, or motion alone to express state.
- Keep small body warnings on `--color-accent-700`, not the base accent.
- Test text contrast whenever a new tint or opacity appears.
- Honor `prefers-reduced-motion`. The global stylesheet shortens animation and
  transition durations automatically.
- Keep functional motion limited to the spinner, disclosure chevron, and theme
  cross-fade.
- Keep fixed actions clear of `safe-area-inset-bottom` and verify that wrapped
  bars do not cover page content. Reserve `--sticky-clearance`, which grows
  fluidly as the viewport narrows.

## Extending the system

Before adding a shared component or screen, check each item:

- [ ] Reuse an existing color, spacing, type, size, rule, and elevation token.
- [ ] Keep the layout flush left and responsive without a page-specific width.
- [ ] Use a 2px rule for sections and a 1px rule for rows.
- [ ] Keep corners square and avoid decorative shadows.
- [ ] Give the screen one clear primary action.
- [ ] Show ingredient source, missing inputs, AI imagery, and nutrition estimates
      wherever relevant.
- [ ] Define default, hover, active, focus, disabled, loading, empty, error, and
      success states.
- [ ] Use native semantics and test the full interaction with a keyboard.
- [ ] Check light and dark themes at 360px, 768px, and 1280px.
- [ ] Test long text, zoom, reduced motion, and the mobile safe area.
- [ ] Add the reusable class contract to this guide when the pattern is new.

If a new component needs a value that no token represents, add a semantic token
to `:root`, define its dark behavior when color-related, and use that token in
the component. Avoid one-off hexadecimal colors, radius values, and repeated
pixel measurements.

## Do and don't

### Do

- Let the modular grid and strong rules organize content.
- Use the accent sparingly and consistently.
- Keep labels and button copy concrete.
- Show assumptions, missing ingredients, allergens, and estimates plainly.
- Preserve user state when returning to an earlier completed step.
- Use grayscale, correctly cropped imagery with an AI label.

### Don't

- Round cards, controls, tags, tabs, or banners.
- Center hero copy or wide button labels.
- Replace structural rules with floating white cards.
- Add a second accent hue, a gradient, glass effects, or ornamental animation.
- Hide a missing ingredient or silently assume a pantry staple.
- Present generated art as a photograph of the finished dish.
- Present nutrition as exact or medical guidance.
- Use emoji or improvised symbols in place of Lucide icons.

import type { CompleteRecipeView } from "./cook-session-state";

function formatMinutes(minutes: number): string {
  return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}

function singleLine(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function appendListSection(lines: string[], title: string, items: string[]): void {
  if (items.length === 0) {
    return;
  }

  lines.push("", `## ${title}`, "", ...items.map((item) => `- ${singleLine(item)}`));
}

export function recipeToMarkdown(recipe: CompleteRecipeView): string {
  const lines = [
    `# ${singleLine(recipe.name)}`,
    "",
    `- **Cuisine:** ${singleLine(recipe.cuisine)}`,
    `- **Servings:** ${recipe.servings}`,
    `- **Total time:** ${formatMinutes(recipe.totalMinutes)}`,
    "",
    "## Ingredients",
    "",
  ];

  for (const ingredient of recipe.ingredients) {
    lines.push(
      `- **${singleLine(ingredient.name)}:** ${singleLine(ingredient.quantity)} _(${singleLine(ingredient.availability)})_`,
    );

    if (ingredient.substitution) {
      lines.push(`  - Substitution: ${singleLine(ingredient.substitution)}`);
    }
  }

  lines.push("", "## Steps", "");

  for (const step of recipe.steps) {
    lines.push(`${step.number}. ${singleLine(step.instruction)}`);

    if (step.durationMinutes !== null) {
      lines.push(`   - Duration: ${formatMinutes(step.durationMinutes)}`);
    }

    if (step.doneWhen) {
      lines.push(`   - Done when: ${singleLine(step.doneWhen)}`);
    }

    if (step.heatLevel) {
      lines.push(`   - Heat level: ${singleLine(step.heatLevel)}`);
    }
  }

  appendListSection(lines, "Tips", recipe.tips);
  appendListSection(lines, "Substitutions", recipe.substitutions);
  appendListSection(lines, "Assumptions", recipe.assumptions);
  appendListSection(lines, "Warnings", recipe.warnings);

  lines.push("", `> **Allergen notice:** ${singleLine(recipe.allergenNotice)}`);

  return `${lines.join("\n")}\n`;
}

export function recipeMarkdownFilename(recipeName: string): string {
  const kebabName = recipeName
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^\p{Letter}\p{Mark}\p{Number}]+/gu, "-")
    .replace(/^-+|-+$/g, "");

  return `${kebabName || "recipe"}.md`;
}

export function downloadRecipeMarkdown(recipe: CompleteRecipeView): boolean {
  if (
    typeof window === "undefined" ||
    typeof document === "undefined" ||
    typeof Blob === "undefined" ||
    typeof URL === "undefined" ||
    typeof URL.createObjectURL !== "function"
  ) {
    return false;
  }

  let downloadUrl: string | null = null;
  let anchor: HTMLAnchorElement | null = null;

  try {
    const blob = new Blob([recipeToMarkdown(recipe)], {
      type: "text/markdown;charset=utf-8",
    });
    downloadUrl = URL.createObjectURL(blob);
    anchor = document.createElement("a");
    anchor.href = downloadUrl;
    anchor.download = recipeMarkdownFilename(recipe.name);
    anchor.hidden = true;
    document.body.append(anchor);
    anchor.click();
    return true;
  } catch {
    return false;
  } finally {
    anchor?.remove();

    if (downloadUrl && typeof URL.revokeObjectURL === "function") {
      URL.revokeObjectURL(downloadUrl);
    }
  }
}

import { expect, it } from "vitest";

import { CookMantraApp } from "@/features/cook-session/CookMantraApp";

import HomePage from "./page";

it("always selects the local cooking app", async () => {
  const page = await HomePage();

  expect(page.type).toBe(CookMantraApp);
});

// The back office in a real browser: an owner signs in with the admin token, adds a dish
// that a till then sells, and reads the day's sales.

import { expect, test } from "@playwright/test";
import { setUpVenue, type Venue } from "./venue";

test.describe.configure({ mode: "serial" });

let venue: Venue;

test.beforeAll(async () => {
  venue = await setUpVenue();
});

test.afterAll(async () => {
  await venue?.admin.dispose();
});

test("an owner adds a dish in the back office and a till can sell it", async ({ page, browser }) => {
  await page.goto("/admin");
  await page.getByLabel("Admin token").fill(process.env.E2E_ADMIN_TOKEN ?? "");
  await page.getByRole("button", { name: "Sign in" }).click();
  // Earlier test runs leave other venues behind: pick this one.
  const picker = page.getByRole("heading", { name: "Choose a location" });
  await expect(picker.or(page.getByRole("link", { name: "Menu" }))).toBeVisible({
    timeout: 60_000,
  });
  if (await picker.isVisible()) {
    const name = (await venue.admin.get("/admin/locations").then((r) => r.json())).find(
      (l: { id: string }) => l.id === venue.locationId,
    ).name as string;
    await page.getByRole("button", { name: new RegExp(name) }).click();
  }
  await page.getByRole("link", { name: "Menu" }).click();
  await expect(page.getByRole("heading", { name: "Menu" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Masala Dosa").first()).toBeVisible();

  await page.getByRole("button", { name: "Add item" }).first().click();
  const editor = page.getByRole("dialog", { name: "Add an item" });
  await editor.getByLabel("Name", { exact: true }).fill("Paneer Tikka");
  await editor.getByLabel("Price (GST included)").fill("19.50");
  await editor.getByLabel("Category").fill("Mains");
  await editor.getByRole("button", { name: "Add item" }).click();
  await expect(editor).toBeHidden();
  await expect(page.getByText("Paneer Tikka").first()).toBeVisible();

  // A freshly paired till sees it straight away.
  const till = await browser.newPage();
  await till.goto("/");
  await till.getByPlaceholder("K7QM-4XPA").fill(venue.pairingCode);
  await till.getByRole("button", { name: "Pair till" }).click();
  await expect(till.getByRole("button", { name: /Paneer Tikka/ })).toBeVisible({ timeout: 60_000 });
  await till.close();

  await page.getByRole("link", { name: "Sales" }).click();
  await expect(page.getByRole("heading", { name: "Sales" })).toBeVisible({ timeout: 60_000 });
});

// A whole table service on the till, in a real browser against the real server: what
// the Sunday showcase does, so a regression here is a regression there.

import { expect, test, type Page } from "@playwright/test";
import { MANAGER_PIN, setUpVenue, type Venue } from "./venue";

test.describe.configure({ mode: "serial" });

let venue: Venue;
let page: Page;

test.beforeAll(async ({ browser }) => {
  venue = await setUpVenue();
  page = await browser.newPage();
  page.on("dialog", (dialog) => void dialog.accept()); // "leave this order?" prompts
});

test.afterAll(async () => {
  await page?.close();
  await venue?.admin.dispose();
});

const ticket = () => page.locator("aside");

async function approve() {
  const pin = page.getByLabel("Manager PIN");
  await expect(pin).toBeVisible();
  await pin.fill(MANAGER_PIN);
  await page.getByRole("button", { name: "Approve" }).click();
}

async function addItem(name: string, choose?: string) {
  await page.getByRole("button", { name: new RegExp(name) }).first().click();
  const sheet = page.getByRole("dialog", { name });
  if (choose) await sheet.getByRole("button", { name: choose }).click();
  await sheet.getByRole("button", { name: /Add to order/ }).click();
  await expect(sheet).toBeHidden();
}

test("pair the till", async () => {
  await page.goto("/");
  await page.getByPlaceholder("K7QM-4XPA").fill(venue.pairingCode);
  await page.getByRole("button", { name: "Pair till" }).click();
  await expect(page.getByRole("button", { name: /Masala Dosa/ })).toBeVisible();
});

test("a table orders, the kitchen gets it, and they order more", async () => {
  await page.getByLabel("Table").fill("7");
  // The dosa needs a spice level before it can go on.
  await page.getByRole("button", { name: /Masala Dosa/ }).first().click();
  const sheet = page.getByRole("dialog", { name: "Masala Dosa" });
  await expect(sheet.getByRole("button", { name: /Add to order/ })).toBeDisabled();
  await sheet.getByRole("button", { name: "Hot" }).click();
  await sheet.getByRole("button", { name: /Add to order/ }).click();
  await addItem("Mango Lassi");
  await page.getByRole("button", { name: "Place order" }).click();
  await expect(ticket().getByText("Table 7")).toBeVisible();
  await page.getByRole("button", { name: "Send to kitchen" }).click();
  await expect(page.getByRole("button", { name: "Reprint kitchen ticket" })).toBeVisible();

  // Leave the table, pick it back up from Open orders, add naan, send just that.
  await page.getByRole("button", { name: "New order" }).click();
  await page.getByRole("button", { name: "Open orders" }).click();
  await page.getByRole("dialog", { name: "Open orders" }).getByText("Table 7").click();
  await expect(ticket().getByText("Masala Dosa")).toBeVisible();
  await addItem("Garlic Naan");
  await page.getByRole("button", { name: /Add 1 item to the order/ }).click();
  await expect(ticket().getByText("not sent")).toBeVisible();
  await page.getByRole("button", { name: "Send 1 new item to kitchen" }).click();
  await expect(ticket().getByText("not sent")).toBeHidden();
});

test("take an item off, with a manager's PIN once the kitchen has it", async () => {
  await page.getByRole("button", { name: "Take off Garlic Naan" }).click();
  const dialog = page.getByRole("dialog", { name: "Take off Garlic Naan" });
  await dialog.getByRole("button", { name: "Customer changed mind" }).click();
  await dialog.getByRole("button", { name: "Take it off" }).click();
  await approve();
  await expect(ticket().getByText("Garlic Naan")).toBeHidden();
  await expect(ticket().getByText("$25.00 owing")).toBeVisible(); // 18.50 + 6.50
});

test("a discount needs a manager; the holiday surcharge doesn't", async () => {
  await page.getByRole("button", { name: "Discount", exact: true }).click();
  const form = page.getByRole("form", { name: "Discount" });
  await form.getByRole("button", { name: "10%" }).click();
  await form.getByLabel("Discount reason").fill("Regulars");
  await form.getByRole("button", { name: "Apply discount" }).click();
  await approve();
  await expect(ticket().getByText("Regulars")).toBeVisible();
  await page.getByRole("button", { name: /Public holiday surcharge 15%/ }).click();
  await expect(ticket().getByText("Public holiday")).toBeVisible();
  // 2500 - 250 = 2250, + 15% = 2587.50 -> 2588 (half up)
  await expect(ticket().getByText("$25.88 owing")).toBeVisible();
});

test("split the bill: half cash with a tip, half on the card terminal", async () => {
  await page.getByRole("button", { name: "Split 2" }).click();
  await page.getByRole("group", { name: "Tip" }).getByRole("button", { name: "10%" }).click();
  await page.getByRole("button", { name: "Pay cash" }).click();
  await expect(ticket().getByText(/Paid \$12\.94 · Cash · tip \$1\.29/)).toBeVisible();
  await page.getByRole("button", { name: "Card on terminal" }).click();
  await page.getByLabel("Terminal receipt or approval number").fill("APPR 004471");
  await page.getByRole("button", { name: "Record card payment" }).click();
  await expect(ticket().getByText("Paid in full")).toBeVisible();
});

test("the receipt is a tax invoice with the GST", async () => {
  await page.getByRole("button", { name: "Receipt" }).click();
  const receipt = page.getByRole("dialog", { name: "Receipt" });
  await expect(receipt.getByText("TAX INVOICE")).toBeVisible();
  await expect(receipt.getByText("ABN 51 824 753 556")).toBeVisible();
  await expect(receipt.getByText("Includes GST")).toBeVisible();
  await expect(receipt.getByText("Tip (Cash)")).toBeVisible();
  await receipt.getByRole("button", { name: "Close" }).click();
});

test("refund part of the cash payment, with a manager's PIN", async () => {
  await page.getByRole("button", { name: "Refund", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Refund" });
  await dialog.getByLabel("Refund amount").fill("5.00");
  await dialog.getByLabel("Refund reason").fill("Lassi was warm");
  await dialog.getByRole("button", { name: "Refund", exact: true }).click();
  await approve();
  await expect(ticket().getByText(/Refunded \$5\.00 · Cash · Lassi was warm/)).toBeVisible();
});

test("void a sent order with a manager's PIN", async () => {
  await page.getByRole("button", { name: "New order" }).click();
  await addItem("Mango Lassi");
  await page.getByRole("button", { name: "Place order" }).click();
  await page.getByRole("button", { name: "Send to kitchen" }).click();
  await page.getByRole("button", { name: "Void order" }).click();
  await page.getByRole("button", { name: "Rang up by mistake" }).click();
  await page.getByRole("button", { name: "Void order" }).last().click();
  await approve();
  await expect(ticket().getByText("Voided.")).toBeVisible();
});

test("mark a dish sold out and back on, with a manager's PIN", async () => {
  await page.getByRole("button", { name: "New order" }).click();
  await page.getByRole("button", { name: "Mark sold out" }).click();
  await page.getByRole("button", { name: /Garlic Naan/ }).click();
  await approve();
  await expect(page.getByRole("button", { name: /Garlic Naan/ })).toContainText("Sold out");
  await page.getByRole("button", { name: /Garlic Naan/ }).click();
  await approve();
  await expect(page.getByRole("button", { name: /Garlic Naan/ })).toContainText("On");
  await page.getByRole("button", { name: "Done marking sold out" }).click();
});

test("the closeout adds it all up", async () => {
  await page.getByRole("link", { name: "Closeout" }).click();
  await page.waitForURL("**/closeout");
  // A dev server builds the page on first visit; the production build is instant.
  await expect(page.getByText("Paid orders and GST")).toBeVisible({ timeout: 60_000 });
  for (const label of ["GST", "Discounts given", "Surcharges", "Refunds", "Tips (not sales)"]) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }
});

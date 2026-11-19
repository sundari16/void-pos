// Sets up a fresh venue through the admin API, as an owner would before service: a
// location with its receipt details and holiday surcharge, a manager, a menu with a
// required modifier group, and a till with a pairing code.

import { request, type APIRequestContext } from "@playwright/test";

export const MANAGER_PIN = "2468";

export interface Venue {
  locationId: string;
  pairingCode: string;
  admin: APIRequestContext;
}

function env(name: string, fallback?: string): string {
  const value = process.env[name] ?? fallback;
  if (!value) throw new Error(`set ${name} to run the end-to-end tests`);
  return value;
}

async function ok<T>(response: Awaited<ReturnType<APIRequestContext["post"]>>): Promise<T> {
  if (!response.ok()) {
    throw new Error(`${response.url()} -> ${response.status()}: ${await response.text()}`);
  }
  return (await response.json()) as T;
}

export async function setUpVenue(): Promise<Venue> {
  const admin = await request.newContext({
    baseURL: env("E2E_API_URL", "http://localhost:8000"),
    extraHTTPHeaders: { Authorization: `Bearer ${env("E2E_ADMIN_TOKEN")}` },
  });
  const stamp = Date.now().toString(36);
  const location = await ok<{ id: string }>(
    await admin.post("/admin/locations", {
      data: { org_name: "Madras Curry House", name: `Showcase ${stamp}` },
    }),
  );
  const loc = location.id;
  await ok(
    await admin.patch(`/admin/locations/${loc}`, {
      data: {
        business_name: "Madras Curry House",
        abn: "51 824 753 556",
        address: "1 Example St, Brisbane QLD 4000",
        receipt_footer: "Nandri! See you again.",
        holiday_surcharge_bp: 1500,
      },
    }),
  );
  await ok(
    await admin.post(`/admin/locations/${loc}/staff`, {
      data: { name: "Asha", role: "manager", pin: MANAGER_PIN },
    }),
  );
  const item = async (name: string, cents: number, category: string) =>
    ok<{ id: string }>(
      await admin.post(`/locations/${loc}/menu/items`, {
        data: { name, base_price_cents: cents, category, tax_rate: 0.1 },
      }),
    );
  const dosa = await item("Masala Dosa", 1850, "Mains");
  await item("Garlic Naan", 450, "Sides");
  await item("Mango Lassi", 650, "Drinks");
  const spice = await ok<{ id: string }>(
    await admin.post(`/locations/${loc}/menu/modifier-groups`, {
      data: { name: "Spice level", min_select: 1, max_select: 1 },
    }),
  );
  for (const name of ["Mild", "Hot"]) {
    await ok(
      await admin.post(`/menu/modifier-groups/${spice.id}/modifiers`, { data: { name } }),
    );
  }
  const attach = await admin.post(`/menu/items/${dosa.id}/modifier-groups/${spice.id}`);
  if (!attach.ok()) throw new Error(`attach -> ${attach.status()}`);
  const till = await ok<{ pairing_code: string }>(
    await admin.post(`/admin/locations/${loc}/devices`, {
      data: { name: "Front till", order_number_start: 1000, order_number_end: 1999 },
    }),
  );
  return { locationId: loc, pairingCode: till.pairing_code, admin };
}

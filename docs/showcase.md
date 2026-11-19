# Showcase runbook

How to run Void POS for a live showcase on one Windows laptop, with the venue's real
Square menu, Stripe in sandbox (test cards, no real money), and tablets or phones as
tills. Every step in "The demo" below is covered by the automated browser tests
(`web/e2e/`), which run on the full Docker stack in CI.

## Before the day

1. **Docker Desktop** installed and running (it needs admin rights, WSL and a restart).
2. **`.env`** in the repo folder (`C:\void-dev\void-pos\.env`, never committed) has:
   - `POSTGRES_PASSWORD` and `VOIDPOS_ADMIN_TOKEN` (already generated);
   - `VOIDPOS_SQUARE_ACCESS_TOKEN`: from developer.squareup.com, your application,
     Credentials, the production access token. It is only used to read the item library;
   - `VOIDPOS_STRIPE_SECRET_KEY` / `VOIDPOS_STRIPE_PUBLISHABLE_KEY`: the **sandbox** keys
     (`sk_test_…`, `pk_test_…`). The server refuses live keys.
3. Start everything from the repo folder:

   ```bash
   docker compose up -d --build --wait
   ```

   `docker compose ps` should show `db`, `api` and `web` healthy.
4. Set up the venue (each command runs inside the API container):

   ```bash
   docker compose exec api void-pos create-location --org "Madras Curry House" --name "Brisbane CBD"
   docker compose exec api void-pos list-locations
   ```

   Using the location id it prints:

   ```bash
   docker compose exec api void-pos import-square --location <id> --dry-run
   docker compose exec api void-pos import-square --location <id>
   docker compose exec api void-pos venue-details --location <id> --business-name "Madras Curry House" --abn "<ABN>" --address "<address>" --phone "<phone>" --footer "Nandri! See you again."
   docker compose exec api void-pos add-staff --location <id> --name "<manager>" --role manager
   ```

   The dry run lists what Square has: items (one per size), categories, GST, modifier
   sets, and anything skipped (variable prices). `add-staff` asks for the manager's PIN.
   The public-holiday surcharge rate is set in the back office (Venue).
5. **Tills:** open `http://<laptop address>:3000/admin` (the laptop's address is shown by
   `ipconfig`, e.g. 192.168.4.26), sign in with the admin token from `.env`, go to Tills,
   add one per tablet with its own order-number range, and issue a pairing code. On each
   tablet or phone (same Wi-Fi), open `http://<laptop address>:3000` and enter the code.
6. Run through "The demo" once, then reset the day's numbers if you want a clean closeout:
   void the test orders, or create a fresh location the night before.

## The demo (about 10 minutes)

1. **Menu from Square.** Back office, Menu: the real dishes, prices, categories, GST and
   modifier sets came straight from Square. Add a special in the back office; it appears on
   the till.
2. **A table.** On the till, type table 7, tap a dish (a required spice level blocks Add
   until picked), add a drink, **Place order**, **Send to kitchen** (an order ticket prints
   if a kitchen printer is set up).
3. **They order more.** **New order**, then **Open orders**, table 7: add naan, **Add 1 item to
   the order**, **Send 1 new item to kitchen** (only the naan goes).
4. **Change of mind.** Tap × on the naan: a reason, and the manager's PIN because the
   kitchen has it (a CANCEL ticket prints).
5. **Discount and surcharge.** **Discount** 10% "Regulars" with the PIN; **Public holiday
   surcharge**. The total updates; GST follows.
6. **Split the bill.** **Split 2**: share 1 cash with a 10% tip; share 2 **Card on this screen
   (Stripe)** with test card 4242 4242 4242 4242, any future date, any CVC. (Or **Card on
   terminal** with the terminal's approval number.) "Paid in full".
7. **Receipt.** **Receipt**: the tax invoice with ABN, GST, tip and payments; print it to the
   receipt printer or from the tablet.
8. **Refund.** **Refund** part of a payment with the PIN; a Stripe payment is refunded through
   Stripe (it shows in the Stripe sandbox dashboard).
9. **Void.** A new order, sent, then **Void order** with a reason and the PIN.
10. **Sold out.** **Mark sold out**, tap a dish, PIN: greyed out on every till.
11. **Close the day.** **Closeout**: money taken, sales, discounts, surcharges, GST, refunds,
    net sales and GST, tips, payments by provider, revenue by class. Back office, Sales: the
    same, plus every order and its receipt, and the full data export.

## If something goes wrong

| What you see | Do this |
|---|---|
| A tablet can't load the till | Same Wi-Fi as the laptop? The laptop's address may have changed: `ipconfig`. Windows may ask to allow Docker through the firewall: allow it on private networks. |
| "Can't reach the Void POS server" | `docker compose ps`; `docker compose up -d` again. Retrying an action is always safe. |
| No Stripe button on the till | Both Stripe keys in `.env`, then `docker compose up -d` (the till checks on load). |
| Square import says the token was refused | A fresh production access token in `.env`, then `docker compose up -d`. |
| "A manager's PIN is needed" | Type the manager's PIN; add one with `add-staff` if there's none. |

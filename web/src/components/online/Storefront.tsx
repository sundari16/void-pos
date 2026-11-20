"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { friendlyError } from "@/lib/commands";
import { groupsFor, localClock, type ChosenModifier } from "@/lib/menu";
import { formatCents } from "@/lib/money";
import { onlineApi } from "@/lib/online/api";
import { maxRedeemablePoints, redemptionValueCents } from "@/lib/online/loyalty";
import { recallCustomer, rememberCustomer } from "@/lib/online/store";
import type { LoyaltyBalance, Storefront as StorefrontData, WebOrder } from "@/lib/online/types";
import {
  draftItemFrom,
  orderLineCount,
  orderTotalCents,
  toLineIns,
  type DraftItem,
} from "@/lib/order";
import type { TillMenuItem } from "@/lib/types";
import { ulid } from "@/lib/ulid";
import { ItemDialog } from "@/components/ItemDialog";
import { MenuGrid } from "@/components/MenuGrid";
import { OrderTicket } from "@/components/OrderTicket";
import { StripeCardDialog } from "@/components/StripeCardDialog";

const STALE_MENU = new Set([
  "item_unavailable",
  "modifier_unavailable",
  "modifier_not_allowed",
  "modifier_selection",
]);

type Stage = "menu" | "checkout" | "done";

interface Form {
  name: string;
  phone: string;
  email: string;
  optIn: boolean;
  fulfillment: "pickup" | "delivery";
  address: string;
  note: string;
  redeem: boolean;
  payNow: boolean;
}

const EMPTY_FORM: Form = {
  name: "",
  phone: "",
  email: "",
  optIn: false,
  fulfillment: "pickup",
  address: "",
  note: "",
  redeem: false,
  payNow: true,
};

export function Storefront({ locationId }: { locationId: string }) {
  const [store, setStore] = useState<StorefrontData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [now, setNow] = useState("00:00:00");

  const [cart, setCart] = useState<DraftItem[]>([]);
  const [dialogItem, setDialogItem] = useState<TillMenuItem | null>(null);
  const [stage, setStage] = useState<Stage>("menu");
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const [loyalty, setLoyalty] = useState<LoyaltyBalance | null>(null);

  const [placed, setPlaced] = useState<WebOrder | null>(null);
  const [stripePay, setStripePay] = useState<{ paymentId: string; amount: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // Minted when checkout opens and held until the order is placed, so a retry after a
  // dropped connection resends the same id and the server replays instead of double-placing.
  const orderId = useRef<string>("");

  const loadMenu = useCallback(async () => {
    try {
      const fresh = await onlineApi.storefront(locationId);
      setStore(fresh);
      setNow(localClock(fresh.menu.timezone));
      setLoadError(null);
    } catch (err) {
      setLoadError(friendlyError(err));
    }
  }, [locationId]);

  useEffect(() => {
    void loadMenu();
  }, [loadMenu]);

  // Keep the clock moving so items drift in and out of their windows.
  const timezone = store?.menu.timezone;
  useEffect(() => {
    if (!timezone) return;
    const tick = setInterval(() => setNow(localClock(timezone)), 30_000);
    return () => clearInterval(tick);
  }, [timezone]);

  // A returning customer (remembered in this browser): fetch their points so redemption can
  // be offered. The server still re-matches and caps, so a stale figure can't overspend.
  useEffect(() => {
    if (!store?.loyalty.enabled) return;
    const remembered = recallCustomer(locationId);
    if (!remembered) return;
    setForm((f) => ({
      ...f,
      name: f.name || remembered.name,
      phone: f.phone || remembered.phone || "",
      email: f.email || remembered.email || "",
      optIn: remembered.marketing_opt_in,
    }));
    onlineApi
      .customerLoyalty(remembered.customer_id)
      .then(setLoyalty)
      .catch(() => setLoyalty(null));
  }, [store?.loyalty.enabled, locationId]);

  const cartTotal = orderTotalCents(cart);
  const cartCount = orderLineCount(cart);
  const redeemCents = store?.loyalty.redeem_cents_per_point ?? 1;
  const redeemablePoints =
    store?.loyalty.enabled && loyalty ? maxRedeemablePoints(loyalty.balance, cartTotal, redeemCents) : 0;
  const redeemValue = form.redeem ? redemptionValueCents(redeemablePoints, redeemCents) : 0;
  const estimatedTotal = Math.max(cartTotal - redeemValue, 0);

  function addToCart(item: TillMenuItem, quantity: number, chosen: ChosenModifier[], note: string) {
    setCart((prev) => [...prev, draftItemFrom(item, quantity, chosen, note)]);
    setDialogItem(null);
  }

  function changeQty(ref: string, delta: number) {
    setCart((prev) =>
      prev.map((it) => (it.ref === ref ? { ...it, quantity: Math.max(1, it.quantity + delta) } : it)),
    );
  }

  function removeItem(ref: string) {
    setCart((prev) => prev.filter((it) => it.ref !== ref));
  }

  function startCheckout() {
    orderId.current = ulid();
    setActionError(null);
    setStage("checkout");
  }

  const contactOk = form.name.trim() !== "" && (form.phone.trim() !== "" || form.email.trim() !== "");
  const addressOk = form.fulfillment !== "delivery" || form.address.trim() !== "";
  const canPlace = cart.length > 0 && contactOk && addressOk && !busy;

  async function placeOrder() {
    if (!store || !canPlace) return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await onlineApi.placeOrder(locationId, {
        order_id: orderId.current,
        customer: {
          name: form.name.trim(),
          phone: form.phone.trim() || null,
          email: form.email.trim() || null,
          marketing_opt_in: form.optIn,
        },
        fulfillment: form.fulfillment,
        delivery_address: form.fulfillment === "delivery" ? form.address.trim() : null,
        customer_note: form.note.trim() || null,
        lines: toLineIns(cart),
        redeem_points: form.redeem ? redeemablePoints : 0,
      });
      rememberCustomer(locationId, {
        customer_id: result.customer_id,
        name: form.name.trim(),
        phone: form.phone.trim() || null,
        email: form.email.trim() || null,
        marketing_opt_in: form.optIn,
      });
      setPlaced(result);
      if (store.online_pay_enabled && form.payNow && result.order.balance_cents > 0) {
        setStripePay({ paymentId: ulid(), amount: result.order.balance_cents });
      } else {
        setStage("done");
      }
    } catch (err) {
      setActionError(friendlyError(err));
      if (err instanceof ApiError && STALE_MENU.has(err.code)) void loadMenu();
    } finally {
      setBusy(false);
    }
  }

  async function onCardPaid(intentId: string) {
    if (!placed || !stripePay) return;
    try {
      await onlineApi.payCard(placed.order.id, {
        payment_id: stripePay.paymentId,
        stripe_payment_intent_id: intentId,
        tip_cents: 0,
      });
      const fresh = await onlineApi.getOrder(placed.order.id).catch(() => placed.order);
      setPlaced({ ...placed, order: fresh });
    } catch (err) {
      setActionError(friendlyError(err));
    } finally {
      setStripePay(null);
      setStage("done");
    }
  }

  function orderAgain() {
    setCart([]);
    setPlaced(null);
    setForm((f) => ({ ...EMPTY_FORM, name: f.name, phone: f.phone, email: f.email, optIn: f.optIn }));
    setStage("menu");
    setActionError(null);
    void loadMenu();
  }

  if (loadError && !store) {
    return (
      <div className="mx-auto max-w-md py-10 text-center">
        <p className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          Couldn&apos;t open the menu: {loadError}
        </p>
      </div>
    );
  }
  if (!store) return <p className="py-10 text-center text-sm text-muted">Loading the menu…</p>;

  return (
    <div>
      <Header venue={store.location_name} count={cartCount} total={cartTotal} />

      {stage === "done" && placed ? (
        <Confirmation order={placed} loyalty={store.loyalty.enabled} onAgain={orderAgain} />
      ) : stage === "checkout" ? (
        <Checkout
          store={store}
          form={form}
          setForm={setForm}
          cart={cart}
          cartTotal={cartTotal}
          redeemablePoints={redeemablePoints}
          redeemValue={redeemValue}
          estimatedTotal={estimatedTotal}
          canPlace={canPlace}
          busy={busy}
          error={actionError}
          onBack={() => setStage("menu")}
          onPlace={placeOrder}
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
          <section>
            {loadError && (
              <p className="mb-3 text-sm text-amber-700 dark:text-amber-400">
                Menu may be out of date: {loadError}
              </p>
            )}
            <MenuGrid items={store.menu.items} now={now} onPick={setDialogItem} />
          </section>
          <aside className="lg:sticky lg:top-4 lg:self-start">
            <Cart
              cart={cart}
              total={cartTotal}
              onQty={changeQty}
              onRemove={removeItem}
              onCheckout={startCheckout}
            />
          </aside>
        </div>
      )}

      {dialogItem && (
        <ItemDialog
          item={dialogItem}
          groups={groupsFor(dialogItem, store.menu)}
          now={now}
          onAdd={addToCart}
          onClose={() => setDialogItem(null)}
        />
      )}
      {stripePay && placed && (
        <StripeCardDialog
          amountCents={stripePay.amount}
          tipCents={0}
          start={() =>
            onlineApi.stripeIntent(placed.order.id, {
              payment_id: stripePay.paymentId,
              tip_cents: 0,
            })
          }
          onPaid={onCardPaid}
          onClose={() => {
            setStripePay(null);
            setStage("done"); // the order is placed; they can pay at pickup instead
          }}
        />
      )}
    </div>
  );
}

function Header({ venue, count, total }: { venue: string; count: number; total: number }) {
  return (
    <header className="mb-4 flex items-center justify-between gap-3 border-b border-border pb-3">
      <h1 className="text-lg font-semibold">{venue}</h1>
      {count > 0 && (
        <span className="text-sm text-muted">
          {count} item{count === 1 ? "" : "s"} · {formatCents(total)}
        </span>
      )}
    </header>
  );
}

function Cart({
  cart,
  total,
  onQty,
  onRemove,
  onCheckout,
}: {
  cart: DraftItem[];
  total: number;
  onQty: (ref: string, delta: number) => void;
  onRemove: (ref: string) => void;
  onCheckout: () => void;
}) {
  return (
    <div className="flex max-h-[calc(100dvh-8rem)] flex-col rounded-2xl border border-border bg-surface">
      <div className="border-b border-border px-4 py-3">
        <h2 className="font-semibold">Your order</h2>
      </div>
      <div className="flex-1 overflow-y-auto px-4">
        {cart.length === 0 ? (
          <p className="py-6 text-sm text-muted">Tap a dish to start your order.</p>
        ) : (
          <OrderTicket items={cart} editable onQtyChange={onQty} onRemove={onRemove} />
        )}
      </div>
      <div className="space-y-3 border-t border-border p-4">
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted">Total</span>
          <span className="text-lg font-semibold tabular-nums">{formatCents(total)}</span>
        </div>
        <button
          onClick={onCheckout}
          disabled={cart.length === 0}
          className="w-full rounded-xl bg-accent px-4 py-3 font-semibold text-white disabled:opacity-40"
        >
          Checkout
        </button>
      </div>
    </div>
  );
}

function Checkout({
  store,
  form,
  setForm,
  cart,
  cartTotal,
  redeemablePoints,
  redeemValue,
  estimatedTotal,
  canPlace,
  busy,
  error,
  onBack,
  onPlace,
}: {
  store: StorefrontData;
  form: Form;
  setForm: (f: (prev: Form) => Form) => void;
  cart: DraftItem[];
  cartTotal: number;
  redeemablePoints: number;
  redeemValue: number;
  estimatedTotal: number;
  canPlace: boolean;
  busy: boolean;
  error: string | null;
  onBack: () => void;
  onPlace: () => void;
}) {
  const set = <K extends keyof Form>(key: K, value: Form[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  return (
    <div className="mx-auto grid max-w-xl gap-5">
      <OrderTicket items={cart} editable={false} onQtyChange={() => {}} onRemove={() => {}} />

      <Field label="Name">
        <input
          value={form.name}
          onChange={(e) => set("name", e.target.value)}
          autoComplete="name"
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Phone">
          <input
            value={form.phone}
            onChange={(e) => set("phone", e.target.value)}
            inputMode="tel"
            autoComplete="tel"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
          />
        </Field>
        <Field label="Email">
          <input
            value={form.email}
            onChange={(e) => set("email", e.target.value)}
            inputMode="email"
            autoComplete="email"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
          />
        </Field>
      </div>
      <p className="-mt-2 text-xs text-muted">We need a phone or an email so we can reach you.</p>

      <Field label="Collection">
        <div className="flex gap-2">
          {(["pickup", "delivery"] as const).map((f) => (
            <button
              key={f}
              onClick={() => set("fulfillment", f)}
              aria-pressed={form.fulfillment === f}
              className={`flex-1 rounded-lg border px-3 py-2 text-sm font-medium capitalize ${
                form.fulfillment === f ? "border-accent bg-accent text-white" : "border-border"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </Field>
      {form.fulfillment === "delivery" && (
        <Field label="Delivery address">
          <input
            value={form.address}
            onChange={(e) => set("address", e.target.value)}
            autoComplete="street-address"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
          />
        </Field>
      )}

      <Field label="Note (optional)">
        <input
          value={form.note}
          onChange={(e) => set("note", e.target.value)}
          maxLength={400}
          placeholder="e.g. nut allergy, leave at door"
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />
      </Field>

      {store.loyalty.enabled && redeemablePoints > 0 && (
        <label className="flex items-center gap-2 rounded-lg border border-border bg-surface p-3 text-sm">
          <input
            type="checkbox"
            checked={form.redeem}
            onChange={(e) => set("redeem", e.target.checked)}
          />
          <span>
            Use {redeemablePoints} points for {formatCents(redeemValue)} off
          </span>
        </label>
      )}

      <label className="flex items-center gap-2 text-sm text-muted">
        <input
          type="checkbox"
          checked={form.optIn}
          onChange={(e) => set("optIn", e.target.checked)}
        />
        <span>Email/text me offers and news</span>
      </label>

      {store.online_pay_enabled ? (
        <Field label="Payment">
          <div className="flex gap-2">
            {[
              { v: true, label: "Pay now by card" },
              { v: false, label: "Pay at pickup" },
            ].map((opt) => (
              <button
                key={String(opt.v)}
                onClick={() => set("payNow", opt.v)}
                aria-pressed={form.payNow === opt.v}
                className={`flex-1 rounded-lg border px-3 py-2 text-sm font-medium ${
                  form.payNow === opt.v ? "border-accent bg-accent text-white" : "border-border"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </Field>
      ) : (
        <p className="text-sm text-muted">Pay when you collect your order.</p>
      )}

      {error && (
        <p className="rounded-lg border border-red-300 bg-red-50 p-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          {error}
        </p>
      )}

      <div className="flex items-center justify-between border-t border-border pt-3">
        <span className="text-sm text-muted">
          {redeemValue > 0 ? (
            <>
              <s>{formatCents(cartTotal)}</s> {formatCents(estimatedTotal)}
            </>
          ) : (
            formatCents(cartTotal)
          )}
        </span>
        <div className="flex gap-2">
          <button
            onClick={onBack}
            disabled={busy}
            className="rounded-xl border border-border px-4 py-2 text-sm font-medium hover:bg-background disabled:opacity-40"
          >
            Back
          </button>
          <button
            onClick={onPlace}
            disabled={!canPlace}
            className="rounded-xl bg-accent px-5 py-2 font-semibold text-white disabled:opacity-40"
          >
            {busy
              ? "Placing…"
              : store.online_pay_enabled && form.payNow
                ? `Place & pay ${formatCents(estimatedTotal)}`
                : "Place order"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Confirmation({
  order,
  loyalty,
  onAgain,
}: {
  order: WebOrder;
  loyalty: boolean;
  onAgain: () => void;
}) {
  const o = order.order;
  const paid = o.balance_cents === 0 && o.payments.length > 0;
  return (
    <div className="mx-auto max-w-md space-y-4 py-6 text-center">
      <div className="text-5xl">✓</div>
      <h2 className="text-xl font-semibold">Order #{o.order_number} placed</h2>
      <p className="text-sm text-muted">
        {o.fulfillment === "delivery" ? "For delivery" : "For pickup"} ·{" "}
        {paid ? "Paid online" : `${formatCents(o.balance_cents)} to pay at pickup`}
      </p>
      {o.delivery_address && <p className="text-sm text-muted">{o.delivery_address}</p>}
      <div className="rounded-xl border border-border bg-surface p-4 text-left text-sm">
        {o.lines
          .filter((l) => l.parent_line_id === null && !l.removed)
          .map((l) => (
            <div key={l.id} className="flex justify-between py-0.5">
              <span>
                {l.quantity}× {l.name}
              </span>
            </div>
          ))}
        <div className="mt-2 flex justify-between border-t border-border pt-2 font-semibold">
          <span>Total</span>
          <span className="tabular-nums">{formatCents(o.total_cents)}</span>
        </div>
      </div>
      {loyalty && (order.points_earned > 0 || order.loyalty_balance > 0) && (
        <p className="text-sm text-accent">
          {order.points_earned > 0 && `You earned ${order.points_earned} points. `}
          Balance: {order.loyalty_balance} points.
        </p>
      )}
      <button
        onClick={onAgain}
        className="rounded-xl border border-border px-5 py-2 text-sm font-medium hover:bg-background"
      >
        Order again
      </button>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-semibold uppercase tracking-wide text-muted">{label}</span>
      {children}
    </label>
  );
}

// A card on the till's screen through Stripe's payment sheet: the customer types the card
// into Stripe's own form, which authorises it; the till then records the payment, and the
// server captures the money as it records it. Card details go from the device straight to
// Stripe and never touch this app or the Void POS server. Which Stripe account (test or live)
// is the server's choice: it hands out the publishable key with each payment intent.

import { initPaymentSheet, initStripe, presentPaymentSheet } from "@stripe/stripe-react-native";
import { cardSheetError } from "./flows";
import type { StripeIntentOut } from "./lib/types";

export const RETURN_URL = "voidpos://stripe-redirect";

export type SheetResult = { authorised: true } | { authorised: false; error: string | null };

export async function takeCardOnScreen(intent: StripeIntentOut, merchant: string): Promise<SheetResult> {
  await initStripe({ publishableKey: intent.publishable_key, urlScheme: "voidpos" });
  const init = await initPaymentSheet({
    merchantDisplayName: merchant,
    paymentIntentClientSecret: intent.client_secret,
    returnURL: RETURN_URL,
  });
  if (init.error) return { authorised: false, error: init.error.message || "Couldn't start the card payment." };
  const shown = await presentPaymentSheet();
  if (shown.error) return { authorised: false, error: cardSheetError(shown.error) };
  return { authorised: true };
}

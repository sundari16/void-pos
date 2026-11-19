// Stripe.js, loaded from js.stripe.com as Stripe requires (card details go from the
// browser straight to Stripe and never touch this app). Only the calls the till uses are
// typed here.

export interface StripeElement {
  mount(el: HTMLElement): void;
  destroy(): void;
}

export interface StripeElements {
  create(type: "payment", options?: Record<string, unknown>): StripeElement;
  submit(): Promise<{ error?: { message?: string } }>;
}

export interface StripeJs {
  elements(options: { clientSecret: string; appearance?: Record<string, unknown> }): StripeElements;
  confirmPayment(options: {
    elements: StripeElements;
    redirect: "if_required";
    confirmParams?: Record<string, unknown>;
  }): Promise<{
    error?: { message?: string };
    paymentIntent?: { id: string; status: string };
  }>;
}

declare global {
  interface Window {
    Stripe?: (publishableKey: string) => StripeJs;
  }
}

const SRC = "https://js.stripe.com/v3/";
let loading: Promise<(key: string) => StripeJs> | null = null;

export function loadStripe(publishableKey: string): Promise<StripeJs> {
  loading ??= new Promise((resolve, reject) => {
    if (window.Stripe) return resolve(window.Stripe);
    const script = document.createElement("script");
    script.src = SRC;
    script.async = true;
    script.onload = () => (window.Stripe ? resolve(window.Stripe) : reject(new Error("Stripe didn't load")));
    script.onerror = () => {
      loading = null;
      reject(new Error("can't load Stripe; check the internet"));
    };
    document.head.appendChild(script);
  });
  return loading.then((make) => make(publishableKey));
}

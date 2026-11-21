// The mobile till's API client: the shared one (lib/api.ts) with a time limit on every
// request. A server that has gone away can leave a request hanging for a long time; giving
// up after a few seconds lets the till switch to working offline instead of freezing.

import { createApi } from "./lib/api";
import { serverBase } from "./server";

export const REQUEST_TIMEOUT_MS = 8_000;

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

/** fetch, aborted after `ms`; an abort surfaces as the client's network_error. */
export function timedFetch(ms: number = REQUEST_TIMEOUT_MS, base: Fetch = (i, init) => fetch(i, init)): Fetch {
  return async (input, init) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ms);
    try {
      return await base(input, { ...init, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  };
}

export const api = createApi(timedFetch(), serverBase);

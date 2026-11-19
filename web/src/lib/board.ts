// Pure helpers for the request board page: status metadata, date formatting and the
// submit-form check. No React, no fetch — unit tested in board.test.ts.

import type { RequestStatus, RequestSubmitIn } from "./adminTypes";

type BadgeTone = "neutral" | "good" | "warn" | "bad" | "accent";

/** The lifecycle, in order, with how each step reads and shows on the board. */
export const STATUS_META: Record<RequestStatus, { label: string; tone: BadgeTone; blurb: string }> = {
  submitted: { label: "Submitted", tone: "neutral", blurb: "On the board, waiting to be looked at." },
  under_review: { label: "Under review", tone: "warn", blurb: "Being considered." },
  planned: { label: "Planned", tone: "accent", blurb: "On the roadmap." },
  shipped: { label: "Shipped", tone: "good", blurb: "Built and released." },
};

export const STATUS_ORDER: RequestStatus[] = ["submitted", "under_review", "planned", "shipped"];

export function statusLabel(status: RequestStatus): string {
  return STATUS_META[status]?.label ?? status;
}

const DATE_FMT = new Intl.DateTimeFormat("en-AU", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "Australia/Brisbane",
});

/** An ISO timestamp as a short Brisbane date, e.g. "20 Nov 2026"; "" when unparseable. */
export function formatDate(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "" : DATE_FMT.format(at);
}

export interface RequestForm {
  title: string;
  body: string;
  author: string;
}

export type RequestErrors = Partial<Record<keyof RequestForm, string>>;

export const MAX_TITLE = 200;
export const MAX_BODY = 4000;
export const MAX_AUTHOR = 120;

/** Validate the submit form the same way the API does, before it's sent. */
export function checkRequest(
  form: RequestForm,
): { value: RequestSubmitIn } | { errors: RequestErrors } {
  const title = form.title.trim();
  const body = form.body.trim();
  const author = form.author.trim();
  const errors: RequestErrors = {};

  if (title.length < 3) errors.title = "Give it a title of at least 3 characters.";
  else if (title.length > MAX_TITLE) errors.title = `Keep the title under ${MAX_TITLE} characters.`;

  if (!body) errors.body = "Say what you'd like, and why it would help.";
  else if (body.length > MAX_BODY) errors.body = `That's longer than ${MAX_BODY} characters.`;

  if (!author) errors.author = "Add a name so others know who asked.";
  else if (author.length > MAX_AUTHOR) errors.author = `Keep the name under ${MAX_AUTHOR} characters.`;

  if (Object.keys(errors).length > 0) return { errors };
  return { value: { title, body, author } };
}

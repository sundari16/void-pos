"use client";

import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { useAdminLocation } from "@/components/admin/AdminProvider";
import {
  Badge,
  Card,
  ErrorNote,
  Field,
  inputClass,
  inputErrorClass,
  PageHeader,
  primaryButton,
  Success,
} from "@/components/admin/ui";
import { errorDetail, type AdminApi } from "@/lib/adminApi";
import {
  checkRequest,
  formatDate,
  STATUS_META,
  STATUS_ORDER,
  type RequestErrors,
  type RequestForm,
} from "@/lib/board";
import type { RequestOut, RequestSort, RequestStatus } from "@/lib/adminTypes";

export default function BoardPage() {
  const { api, location } = useAdminLocation();
  const [requests, setRequests] = useState<RequestOut[] | null>(null);
  const [sort, setSort] = useState<RequestSort>("top");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRequests(await api.requests(sort));
      setError(null);
    } catch (err) {
      setError(errorDetail(err));
    }
  }, [api, sort]);

  useEffect(() => {
    load();
  }, [load]);

  // Swap one request in place after an upvote or a status change — no full reload.
  const replace = useCallback((next: RequestOut) => {
    setRequests((list) => (list ? list.map((r) => (r.id === next.id ? next : r)) : list));
  }, []);

  return (
    <div className="space-y-4">
      <PageHeader title="Request board">
        <div className="flex gap-1 rounded-lg border border-border p-0.5">
          {(["top", "new"] as const).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setSort(key)}
              aria-pressed={sort === key}
              className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                sort === key ? "bg-accent text-white" : "text-muted hover:text-foreground"
              }`}
            >
              {key === "top" ? "Most voted" : "Newest"}
            </button>
          ))}
        </div>
      </PageHeader>

      <p className="text-sm text-muted">
        Ask for a feature, upvote the ones you want, and see where each one stands. Nothing is
        hidden — every request keeps its status and dates out in the open.
      </p>

      <SuggestForm
        api={api}
        defaultAuthor={location.business_name ?? location.name}
        onAdded={load}
      />

      {error && <ErrorNote>{error}</ErrorNote>}
      {!requests && !error && <p className="text-sm text-muted">Loading…</p>}
      {requests && requests.length === 0 && (
        <Card>
          <p className="text-muted">No requests yet. Be the first to suggest one.</p>
        </Card>
      )}
      {requests && requests.length > 0 && (
        <ul className="space-y-3">
          {requests.map((req) => (
            <RequestRow key={req.id} api={api} request={req} onChanged={replace} />
          ))}
        </ul>
      )}
    </div>
  );
}

function SuggestForm({
  api,
  defaultAuthor,
  onAdded,
}: {
  api: AdminApi;
  defaultAuthor: string;
  onAdded: () => Promise<void>;
}) {
  const [form, setForm] = useState<RequestForm>({ title: "", body: "", author: defaultAuthor });
  const [errors, setErrors] = useState<RequestErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [added, setAdded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const uid = useId();

  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = checkRequest(form);
    if ("errors" in checked) {
      setErrors(checked.errors);
      return;
    }
    setErrors({});
    setServerError(null);
    setAdded(null);
    setBusy(true);
    try {
      const created = await api.submitRequest(checked.value);
      setForm({ title: "", body: "", author: defaultAuthor });
      setAdded(`“${created.title}” is on the board.`);
      await onAdded();
    } catch (err) {
      setServerError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  const cls = (key: keyof RequestForm) => `${inputClass} ${errors[key] ? inputErrorClass : ""}`;

  return (
    <Card title="Suggest a feature">
      <form onSubmit={submit} className="space-y-3" noValidate>
        <Field label="Title" htmlFor={`${uid}-title`} error={errors.title}>
          <input
            id={`${uid}-title`}
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            maxLength={200}
            className={cls("title")}
          />
        </Field>
        <Field label="Details" htmlFor={`${uid}-body`} error={errors.body}>
          <textarea
            id={`${uid}-body`}
            value={form.body}
            onChange={(e) => setForm((f) => ({ ...f, body: e.target.value }))}
            rows={3}
            maxLength={4000}
            className={cls("body")}
          />
        </Field>
        <Field
          label="Your name"
          htmlFor={`${uid}-author`}
          error={errors.author}
          hint="Shown on the board next to your request"
        >
          <input
            id={`${uid}-author`}
            value={form.author}
            onChange={(e) => setForm((f) => ({ ...f, author: e.target.value }))}
            maxLength={120}
            className={cls("author")}
          />
        </Field>
        {serverError && <ErrorNote>{serverError}</ErrorNote>}
        {added && <Success>{added}</Success>}
        <button type="submit" disabled={busy} className={primaryButton}>
          {busy ? "Posting…" : "Post request"}
        </button>
      </form>
    </Card>
  );
}

function RequestRow({
  api,
  request,
  onChanged,
}: {
  api: AdminApi;
  request: RequestOut;
  onChanged: (next: RequestOut) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const meta = STATUS_META[request.status];

  async function run(action: () => Promise<RequestOut>) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await action());
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex gap-3 rounded-2xl border border-border bg-surface p-4">
      <VoteButton
        votes={request.votes}
        voted={request.voted}
        busy={busy}
        onClick={() => run(() => api.upvoteRequest(request.id))}
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="font-semibold">{request.title}</h3>
          <Badge tone={meta.tone}>{meta.label}</Badge>
        </div>
        <p className="mt-1 whitespace-pre-wrap text-sm">{request.body}</p>
        <p className="mt-2 text-xs text-muted">
          {request.author} · Submitted {formatDate(request.created_at)}
          {request.updated_at > request.created_at &&
            ` · Updated ${formatDate(request.updated_at)}`}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label htmlFor={`status-${request.id}`} className="text-xs font-medium text-muted">
            Set status
          </label>
          <select
            id={`status-${request.id}`}
            value={request.status}
            disabled={busy}
            onChange={(e) => run(() => api.setRequestStatus(request.id, e.target.value as RequestStatus))}
            className="rounded-lg border border-border bg-background px-2 py-1 text-sm"
          >
            {STATUS_ORDER.map((status) => (
              <option key={status} value={status}>
                {STATUS_META[status].label}
              </option>
            ))}
          </select>
        </div>
        {error && <p className="mt-2 text-sm text-red-700 dark:text-red-300">{error}</p>}
      </div>
    </li>
  );
}

function VoteButton({
  votes,
  voted,
  busy,
  onClick,
}: {
  votes: number;
  voted: boolean;
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-pressed={voted}
      aria-label={`Upvote (${votes})`}
      className={`flex h-14 w-12 shrink-0 flex-col items-center justify-center rounded-xl border font-semibold disabled:opacity-50 ${
        voted ? "border-accent bg-accent/10 text-accent" : "border-border hover:bg-background"
      }`}
    >
      <span aria-hidden className="text-base leading-none">
        ▲
      </span>
      <span className="mt-0.5 text-sm">{votes}</span>
    </button>
  );
}

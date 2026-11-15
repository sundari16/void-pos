import { config } from "@/lib/config";

/** Shown when the till doesn't yet know its location / device. */
export function ConfigNotice() {
  return (
    <div className="rounded-xl border border-border bg-surface p-6 text-sm">
      <h2 className="text-base font-semibold">This till isn&apos;t configured yet</h2>
      <p className="mt-2 text-muted">
        Set the location and device the POS rings orders against, then restart{" "}
        <code className="rounded bg-background px-1">npm run dev</code>.
      </p>
      <ol className="mt-4 list-decimal space-y-1 pl-5 text-muted">
        <li>
          Run the backend and seed it:{" "}
          <code className="rounded bg-background px-1">python scripts/seed_dev.py</code>
        </li>
        <li>
          Copy the printed values into <code className="rounded bg-background px-1">web/.env.local</code>.
        </li>
      </ol>
      <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-xs">
        <dt className="text-muted">API base</dt>
        <dd>{config.apiBase}</dd>
        <dt className="text-muted">Location</dt>
        <dd>{config.locationId || <span className="text-red-500">(unset)</span>}</dd>
        <dt className="text-muted">Device</dt>
        <dd>{config.deviceId || <span className="text-red-500">(unset)</span>}</dd>
      </dl>
    </div>
  );
}

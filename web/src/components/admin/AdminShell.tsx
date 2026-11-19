"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";
import { loadAdmin, sessionStore } from "@/lib/adminSession";
import { useAdmin } from "./AdminProvider";
import { ErrorNote, Notice, primaryButton, secondaryButton, smallButton } from "./ui";

const NAV = [
  { href: "/admin/menu", label: "Menu" },
  { href: "/admin/staff", label: "Staff" },
  { href: "/admin/tills", label: "Tills" },
  { href: "/admin/venue", label: "Venue" },
  { href: "/admin/sales", label: "Sales" },
  { href: "/admin/board", label: "Board" },
];

/** Sign in, pick a location, then the back office with its left-hand nav. */
export function AdminShell({ children }: { children: ReactNode }) {
  const { ready, token, location } = useAdmin();
  if (!ready) return <p className="text-sm text-muted">Loading…</p>;
  if (!token) return <SignIn />;
  if (!location) return <LocationPicker />;
  return (
    <div className="md:grid md:grid-cols-[11rem_minmax(0,1fr)] md:gap-6">
      <AdminNav />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function AdminNav() {
  const pathname = usePathname();
  const { location, locations, chooseLocation, signOut } = useAdmin();
  return (
    <aside className="mb-4 md:mb-0">
      <div className="md:sticky md:top-20 md:space-y-4">
        <div className="hidden md:block">
          <p className="text-xs font-medium uppercase tracking-wide text-muted">Back office</p>
          <p className="mt-0.5 font-semibold leading-tight">{location?.name}</p>
          {locations.length > 1 && (
            <button
              onClick={() => chooseLocation(null)}
              className="mt-1 text-xs font-medium text-accent hover:underline"
            >
              Switch location
            </button>
          )}
        </div>
        <nav aria-label="Back office" className="flex gap-1 overflow-x-auto md:flex-col">
          {NAV.map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`whitespace-nowrap rounded-lg px-3 py-2 font-medium ${
                  active
                    ? "bg-accent text-white"
                    : "text-muted hover:bg-surface hover:text-foreground"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-2 flex flex-wrap gap-2 md:mt-0 md:flex-col md:border-t md:border-border md:pt-4">
          <Link href="/" className={`${smallButton} text-center`}>
            ← Back to the till
          </Link>
          <button onClick={() => signOut()} className={smallButton}>
            Sign out
          </button>
        </div>
      </div>
    </aside>
  );
}

function SignIn() {
  const { signIn, notice, retry } = useAdmin();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await signIn(token);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mx-auto mt-8 max-w-md space-y-4 rounded-2xl border border-border bg-surface p-6"
    >
      <div>
        <h1 className="text-lg font-semibold">Back office</h1>
        <p className="mt-1 text-sm text-muted">
          Paste the venue&apos;s admin token (<span className="font-mono">VOIDPOS_ADMIN_TOKEN</span>{" "}
          on the venue server). It stays in this tab only and is forgotten when you close it.
        </p>
      </div>

      {notice && (
        <Notice>
          {notice}{" "}
          {/* A saved sign-in that couldn't be checked (server unreachable) can be retried. */}
          {loadAdmin(sessionStore()).token && (
            <button type="button" onClick={retry} className="font-semibold underline">
              Try again
            </button>
          )}
        </Notice>
      )}

      <div>
        <label htmlFor="admin-token" className="mb-1 block text-sm font-medium">
          Admin token
        </label>
        <input
          id="admin-token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          autoFocus
          className="w-full rounded-lg border border-border bg-background px-3 py-2 font-mono"
        />
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}

      <button type="submit" disabled={busy || !token.trim()} className={`w-full ${primaryButton}`}>
        {busy ? "Checking…" : "Sign in"}
      </button>
    </form>
  );
}

function LocationPicker() {
  const { locations, chooseLocation, signOut } = useAdmin();
  return (
    <div className="mx-auto mt-8 max-w-md space-y-4 rounded-2xl border border-border bg-surface p-6">
      <div>
        <h1 className="text-lg font-semibold">Choose a location</h1>
        <p className="mt-1 text-sm text-muted">The back office works on one location at a time.</p>
      </div>
      {locations.length === 0 ? (
        <Notice>
          There are no locations yet. Create one on the venue server with{" "}
          <span className="font-mono">void-pos create-location</span>.
        </Notice>
      ) : (
        <ul className="space-y-2">
          {locations.map((loc) => (
            <li key={loc.id}>
              <button
                onClick={() => chooseLocation(loc.id)}
                className="w-full rounded-xl border border-border px-4 py-3 text-left hover:bg-background"
              >
                <span className="block font-semibold">{loc.name}</span>
                <span className="block text-sm text-muted">
                  {loc.business_name ?? "No business name yet"} · {loc.timezone}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <button onClick={() => signOut()} className={`w-full ${secondaryButton}`}>
        Sign out
      </button>
    </div>
  );
}

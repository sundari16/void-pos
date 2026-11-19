"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

// A customer reaches the storefront at /order/<location id>. A venue can set a default
// location at build time so /order redirects straight to it; otherwise this lets you open
// one by id (the operator shares the full link with customers).
const DEFAULT_LOCATION = (process.env.NEXT_PUBLIC_DEFAULT_LOCATION ?? "").trim();

export default function OrderLanding() {
  const router = useRouter();
  const [locationId, setLocationId] = useState("");

  useEffect(() => {
    if (DEFAULT_LOCATION) router.replace(`/order/${DEFAULT_LOCATION}`);
  }, [router]);

  if (DEFAULT_LOCATION) return <p className="text-sm text-muted">Opening the menu…</p>;

  return (
    <div className="mx-auto max-w-md space-y-4 py-10">
      <h1 className="text-xl font-semibold">Order online</h1>
      <p className="text-sm text-muted">
        Open your venue&apos;s ordering page. Your venue will have shared a link ending in a
        location id.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (locationId.trim()) router.push(`/order/${locationId.trim()}`);
        }}
        className="flex gap-2"
      >
        <input
          value={locationId}
          onChange={(e) => setLocationId(e.target.value)}
          placeholder="Location id"
          aria-label="Location id"
          className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />
        <button className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white">
          Open
        </button>
      </form>
    </div>
  );
}

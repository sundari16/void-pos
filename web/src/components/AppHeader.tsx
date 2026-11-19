"use client";

import Link from "next/link";
import { useState } from "react";
import { useTill } from "./TillProvider";

export function AppHeader() {
  const { till, unpair } = useTill();
  const [confirming, setConfirming] = useState(false);

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-surface/90 backdrop-blur">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2 px-4 py-3">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-accent text-sm text-white">
            V
          </span>
          <span>Void POS</span>
        </Link>

        <div className="flex items-center gap-1 text-sm">
          <nav className="flex items-center gap-1">
            {till && (
              <>
                <Link
                  href="/"
                  className="rounded-md px-3 py-1.5 font-medium text-muted hover:bg-background hover:text-foreground"
                >
                  Order
                </Link>
                <Link
                  href="/closeout"
                  className="rounded-md px-3 py-1.5 font-medium text-muted hover:bg-background hover:text-foreground"
                >
                  Closeout
                </Link>
              </>
            )}
            <Link
              href="/admin"
              className="rounded-md px-3 py-1.5 font-medium text-muted hover:bg-background hover:text-foreground"
            >
              Back office
            </Link>
          </nav>
          {till && (
            <>
              <span className="ml-2 hidden text-muted sm:inline">
                {till.deviceName} · {till.locationName}
              </span>
              <button
                onClick={() => {
                  if (confirming) unpair();
                  setConfirming(!confirming);
                }}
                onBlur={() => setConfirming(false)}
                className="ml-1 rounded-md px-2 py-1.5 text-xs font-medium text-muted hover:bg-background hover:text-foreground"
              >
                {confirming ? "Tap again to unpair" : "Unpair"}
              </button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
